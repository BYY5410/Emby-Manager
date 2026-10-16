# -*- coding: utf-8 -*-
"""
支付网关适配层（Payment Gateways）

设计原则：
1. 本模块不 import app，不直接读写数据库；渠道对象 duck-typing（具备 get_credentials()/get_config()）。
2. 敏感凭证由 app 层 Fernet 加密落库，本模块只在单次请求生命周期内拿到明文，用后即弃；
   任何日志都不得打印完整凭证/签名串。
3. 每个适配器实现三件事：
   - create_order：服务端下单，返回支付链接(pay_url)或二维码串(qr_code)
   - verify_notify：验证渠道异步回调（防伪造/防篡改/防重放由调用方配合流水号与时间窗完成）
   - query_order：服务端主动查单
4. static_qrcode（个人收款码）不具备 notify/query 能力（系统无法核实个人码到账）。

支持类型：
  static_qrcode 个人收款码
  alipay_f2f    支付宝当面付（alipay.trade.precreate，RSA2）
  wechat_native 微信 Native 支付（APIv3，RSA + AES-128-GCM）
  epay          彩虹易支付通用协议（MD5，兼容大量易支付二开站点）
  hupijiao      虎皮椒（MD5）
  stripe        Stripe Checkout（HMAC-SHA256 webhook）
  jd            京东收银（SHA-256/RSA 标准签名骨架，需以商户后台文档联调字段）
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse as urllib_parse
from decimal import Decimal, InvalidOperation

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography import x509

TIMEOUT = 12
WECHAT_NOTIFY_WINDOW = 300      # 微信回调时间窗（秒）
STRIPE_NOTIFY_WINDOW = 300      # Stripe webhook 时间容差（秒）


# ============================== 基础结构 ==============================

class NotifyResult(object):
    """回调验签结果（失败时 ok=False，reason 用于日志，禁止记录敏感报文）"""
    def __init__(self, ok, order_no=None, amount_yuan=None, trade_no=None,
                 paid=True, reason=''):
        self.ok = ok
        self.order_no = order_no
        self.amount_yuan = amount_yuan
        self.trade_no = trade_no
        self.paid = paid
        self.reason = reason

    def __repr__(self):
        return '<NotifyResult ok=%s order=%s amount=%s trade=%s reason=%s>' % (
            self.ok, self.order_no, self.amount_yuan, self.trade_no, self.reason)


# ============================== 密码学工具 ==============================

def md5_hex(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def hmac_sha256_hex(secret, message):
    return hmac.new(secret.encode('utf-8'), message.encode('utf-8'),
                    hashlib.sha256).hexdigest()


def _load_private_key(pem):
    return serialization.load_pem_private_key(
        pem.encode('utf-8'), password=None)


def _load_public_key(pem):
    """公钥可能是 PUBLIC KEY 或证书（CERTIFICATE）PEM"""
    pem = pem.strip()
    if 'BEGIN CERTIFICATE' in pem:
        return x509.load_pem_x509_certificate(pem.encode('utf-8')).public_key()
    return serialization.load_pem_public_key(pem.encode('utf-8'))


def rsa_sign_sha256(private_key_pem, message):
    """SHA256withRSA / WECHATPAY2-SHA256-RSA2048 签名，返回 base64"""
    key = _load_private_key(private_key_pem)
    signature = key.sign(message.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode('utf-8')


def rsa_verify_sha256(public_key_pem, signature_b64, message):
    try:
        pub = _load_public_key(public_key_pem)
        pub.verify(base64.b64decode(signature_b64), message.encode('utf-8'),
                   padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, ValueError, Exception):
        return False


def aes_gcm_decrypt(key_str, nonce, ciphertext_b64, associated_data=None):
    """微信 APIv3 回调解密：key 为 APIV3 密钥(32字节字符串)"""
    aes = AESGCM(key_str.encode('utf-8'))
    return aes.decrypt(nonce.encode('utf-8'),
                       base64.b64decode(ciphertext_b64),
                       (associated_data or '').encode('utf-8')).decode('utf-8')


def gen_rsa_keypair_pem():
    """仅用于测试：生成 (私钥PEM, 自签证书PEM/公钥PEM)"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


def sorted_sign_content(params, exclude=('sign',)):
    """按键 ASCII 升序，过滤空值与排除键，以 k=v&k=v 拼接（值不做 urlencode）"""
    pairs = []
    for k in sorted(params.keys()):
        if k in exclude:
            continue
        v = params.get(k)
        if v is None or v == '':
            continue
        pairs.append('%s=%s' % (k, v))
    return '&'.join(pairs)


def to_yuan(value):
    """安全转两位小数十进金额；非法抛 ValueError"""
    try:
        d = Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise ValueError('bad amount')
    if d < Decimal('0.01'):
        raise ValueError('bad amount')
    return d


def _post_form(url, data, headers=None):
    return requests.post(url, data=data, headers=headers, timeout=TIMEOUT)


def _post_json(url, payload, headers=None):
    h = {'Content-Type': 'application/json'}
    if headers:
        h.update(headers)
    return requests.post(url, data=json.dumps(payload), headers=h, timeout=TIMEOUT)


# ============================== 适配器基类 ==============================

class PaymentGateway(object):
    type_id = ''
    label = ''
    supports_notify = True
    supports_query = True

    def cred(self, gw, key, default=''):
        return gw.get_credentials().get(key, default)

    def conf(self, gw, key, default=''):
        return gw.get_config().get(key, default)

    def create_order(self, gw, order_no, amount_yuan, subject, notify_url,
                     return_url):
        raise NotImplementedError

    def verify_notify(self, gw, raw_body, headers, args, form):
        raise NotImplementedError

    def query_order(self, gw, order_no):
        """返回 {'paid': bool, 'trade_no': str, 'amount_yuan': Decimal}"""
        raise NotImplementedError

    def ack(self, ok):
        """返回 Flask 可识别的 (body, http_status, headers_dict)"""
        return ('success' if ok else 'fail'), (200 if ok else 400), \
            {'Content-Type': 'text/plain; charset=utf-8'}


class StaticQrcodeGateway(PaymentGateway):
    type_id = 'static_qrcode'
    label = '个人收款码'
    supports_notify = False
    supports_query = False

    def create_order(self, *a, **kw):
        return {}  # 页面直接展示渠道保存的收款码图片


# ============================== 支付宝当面付 ==============================

class AlipayF2FGateway(PaymentGateway):
    type_id = 'alipay_f2f'
    label = '支付宝当面付'

    DEFAULT_GATEWAY = 'https://openapi.alipay.com/gateway.do'

    def _gateway(self, gw):
        return (self.conf(gw, 'gateway') or self.DEFAULT_GATEWAY).strip()

    def _signed_params(self, gw, method, biz, notify_url=None):
        params = {
            'app_id': self.cred(gw, 'app_id'),
            'method': method,
            'charset': 'utf-8',
            'sign_type': 'RSA2',
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'version': '3.0',
            'biz_content': json.dumps(biz, ensure_ascii=False, separators=(',', ':')),
        }
        if notify_url:
            params['notify_url'] = notify_url
        params['sign'] = rsa_sign_sha256(self.cred(gw, 'app_private_key'),
                                         sorted_sign_content(params))
        return params

    def create_order(self, gw, order_no, amount_yuan, subject, notify_url,
                     return_url):
        biz = {
            'out_trade_no': order_no,
            'total_amount': str(Decimal(amount_yuan).quantize(Decimal('0.01'))),
            'subject': subject or ('充值订单 %s' % order_no),
        }
        resp = _post_form(self._gateway(gw), self._signed_params(gw, 'alipay.trade.precreate', biz, notify_url))
        data = resp.json()
        inner = data.get('alipay_trade_precreate_response') or {}
        if inner.get('code') != '10000' or not inner.get('qr_code'):
            raise RuntimeError('alipay precreate failed: %s' % inner.get('sub_msg') or inner.get('msg') or resp.text[:200])
        return {'qr_code': inner['qr_code']}

    def verify_notify(self, gw, raw_body, headers, args, form):
        sign = form.get('sign', '')
        if not sign:
            return NotifyResult(False, reason='no sign')
        message = sorted_sign_content(form, exclude=('sign',))
        if not rsa_verify_sha256(self.cred(gw, 'alipay_public_key'), sign, message):
            return NotifyResult(False, reason='bad sign')
        if form.get('trade_status') not in ('TRADE_SUCCESS', 'TRADE_FINISHED'):
            return NotifyResult(False, paid=False, reason='not success status')
        try:
            amount = to_yuan(form.get('total_amount'))
        except ValueError:
            return NotifyResult(False, reason='bad amount')
        return NotifyResult(True, order_no=form.get('out_trade_no'),
                            amount_yuan=amount, trade_no=form.get('trade_no'))

    def query_order(self, gw, order_no):
        biz = {'out_trade_no': order_no}
        resp = _post_form(self._gateway(gw), self._signed_params(gw, 'alipay.trade.query', biz))
        data = resp.json().get('alipay_trade_query_response') or {}
        paid = data.get('trade_status') in ('TRADE_SUCCESS', 'TRADE_FINISHED')
        return {
            'paid': paid,
            'trade_no': data.get('trade_no'),
            'amount_yuan': to_yuan(data['total_amount']) if paid and data.get('total_amount') else None,
        }


# ============================== 微信 Native（APIv3） ==============================

class WechatNativeGateway(PaymentGateway):
    type_id = 'wechat_native'
    label = '微信Native支付'

    BASE = 'https://api.mch.weixin.qq.com'
    _platform_certs = {}  # {(mchid, serial): pem}

    def _base(self, gw):
        return (self.conf(gw, 'base_url') or self.BASE).strip().rstrip('/')

    def _authorization(self, gw, method, path, body):
        ts = str(int(time.time()))
        nonce = os.urandom(16).hex()
        message = '%s\n%s\n%s\n%s\n%s\n' % (method, path, ts, nonce, body)
        signature = rsa_sign_sha256(self.cred(gw, 'merchant_private_key'), message)
        return ('WECHATPAY2-SHA256-RSA2048 mchid="%s",nonce_str="%s",timestamp="%s",'
                'serial="%s",signature="%s"') % (
            self.cred(gw, 'mchid'), nonce, ts,
            self.cred(gw, 'merchant_serial'), signature)

    def _platform_cert(self, gw, serial):
        """取微信支付平台证书：优先凭证中手填，否则下载解密并缓存"""
        mchid = self.cred(gw, 'mchid')
        cache_key = (mchid, serial)
        if cache_key in self._platform_certs:
            return self._platform_certs[cache_key]
        manual = (self.cred(gw, 'platform_cert') or '').strip()
        if manual:
            self._platform_certs[cache_key] = manual
            return manual
        path = '/v3/certificates'
        resp = requests.get(
            self._base(gw) + path,
            headers={'Authorization': self._authorization(gw, 'GET', path, ''),
                     'Accept': 'application/json'},
            timeout=TIMEOUT)
        for item in resp.json().get('data', []):
            enc = item.get('encrypt_certificate') or {}
            pem = aes_gcm_decrypt(self.cred(gw, 'apiv3_key'),
                                  enc.get('nonce'), enc.get('ciphertext'),
                                  enc.get('associated_data'))
            self._platform_certs[(mchid, item.get('serial_no'))] = pem
        return self._platform_certs.get(cache_key)

    def create_order(self, gw, order_no, amount_yuan, subject, notify_url,
                     return_url):
        path = '/v3/pay/transactions/native'
        body = {
            'appid': self.cred(gw, 'appid'),
            'mchid': self.cred(gw, 'mchid'),
            'description': subject or ('充值订单 %s' % order_no),
            'out_trade_no': order_no,
            'notify_url': notify_url,
            'amount': {
                'total': int((Decimal(amount_yuan) * 100).to_integral_value()),
                'currency': 'CNY',
            },
        }
        body_str = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        resp = requests.post(
            self._base(gw) + path, data=body_str.encode('utf-8'),
            headers={'Authorization': self._authorization(gw, 'POST', path, body_str),
                     'Content-Type': 'application/json', 'Accept': 'application/json'},
            timeout=TIMEOUT)
        data = resp.json()
        if resp.status_code != 200 or not data.get('code_url'):
            raise RuntimeError('wechat native failed: %s' % resp.text[:200])
        return {'qr_code': data['code_url']}

    def verify_notify(self, gw, raw_body, headers, args, form):
        def h(name):
            return headers.get(name) or headers.get(name.title()) or headers.get(name.lower())
        ts = h('Wechatpay-Timestamp')
        nonce = h('Wechatpay-Nonce')
        signature = h('Wechatpay-Signature')
        serial = h('Wechatpay-Serial')
        if not all([ts, nonce, signature, serial]):
            return NotifyResult(False, reason='missing headers')
        try:
            if abs(int(time.time()) - int(ts)) > WECHAT_NOTIFY_WINDOW:
                return NotifyResult(False, reason='timestamp expired')
        except ValueError:
            return NotifyResult(False, reason='bad timestamp')
        cert_pem = self._platform_cert(gw, serial)
        if not cert_pem:
            return NotifyResult(False, reason='platform cert not found')
        message = '%s\n%s\n%s\n' % (ts, nonce, raw_body)
        if not rsa_verify_sha256(cert_pem, signature, message):
            return NotifyResult(False, reason='bad sign')
        try:
            event = json.loads(raw_body)
            resource = event['resource']
            plain = aes_gcm_decrypt(self.cred(gw, 'apiv3_key'),
                                    resource['nonce'], resource['ciphertext'],
                                    resource.get('associated_data'))
            data = json.loads(plain)
        except Exception:
            return NotifyResult(False, reason='decrypt failed')
        if data.get('trade_state') != 'SUCCESS':
            return NotifyResult(False, paid=False, reason='trade_state not success')
        try:
            amount = (Decimal(str(int(data['amount']['total']))) / 100).quantize(Decimal('0.01'))
        except (KeyError, InvalidOperation):
            return NotifyResult(False, reason='bad amount')
        return NotifyResult(True, order_no=data.get('out_trade_no'),
                            amount_yuan=amount, trade_no=data.get('transaction_id'))

    def query_order(self, gw, order_no):
        path = '/v3/pay/transactions/out-trade-no/%s?mchid=%s' % (
            order_no, self.cred(gw, 'mchid'))
        resp = requests.get(
            self._base(gw) + path,
            headers={'Authorization': self._authorization(gw, 'GET', path, ''),
                     'Accept': 'application/json'},
            timeout=TIMEOUT)
        data = resp.json()
        if data.get('trade_state') != 'SUCCESS':
            return {'paid': False, 'trade_no': data.get('transaction_id'), 'amount_yuan': None}
        amount = (Decimal(str(int(data['amount']['total']))) / 100).quantize(Decimal('0.01'))
        return {'paid': True, 'trade_no': data.get('transaction_id'), 'amount_yuan': amount}

    def ack(self, ok):
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        if ok:
            return json.dumps({'code': 'SUCCESS', 'message': '成功'}), 200, headers
        return json.dumps({'code': 'FAIL', 'message': '验签失败'}), 401, headers


# ============================== 彩虹易支付 ==============================

class EpayGateway(PaymentGateway):
    """彩虹易支付（及兼容协议站点）：MD5 签名"""
    type_id = 'epay'
    label = '易支付'

    def _gateway(self, gw):
        return (self.conf(gw, 'gateway') or '').strip().rstrip('/')

    def _key(self, gw):
        return self.cred(gw, 'key')

    def _sign(self, gw, params):
        # 标准：排序去空 k=v 拼接后直接拼接商户密钥，MD5 小写
        return md5_hex(sorted_sign_content(params, exclude=('sign', 'sign_type')) + self._key(gw))

    def create_order(self, gw, order_no, amount_yuan, subject, notify_url,
                     return_url):
        params = {
            'pid': self.cred(gw, 'pid'),
            'type': self.conf(gw, 'pay_type', 'alipay') or 'alipay',
            'out_trade_no': order_no,
            'notify_url': notify_url,
            'return_url': return_url,
            'name': subject or ('充值订单 %s' % order_no),
            'money': str(Decimal(amount_yuan).quantize(Decimal('0.01'))),
            'clientip': '',
        }
        params['sign'] = self._sign(gw, params)
        params['sign_type'] = 'MD5'
        qs = urllib_parse.urlencode(params)
        return {'pay_url': '%s/submit.php?%s' % (self._gateway(gw), qs)}

    def verify_notify(self, gw, raw_body, headers, args, form):
        sign = form.get('sign', '')
        if not sign or not hmac.compare_digest(sign, self._sign(gw, form)):
            return NotifyResult(False, reason='bad sign')
        if form.get('trade_status') != 'TRADE_SUCCESS':
            return NotifyResult(False, paid=False, reason='not success')
        try:
            amount = to_yuan(form.get('money'))
        except ValueError:
            return NotifyResult(False, reason='bad amount')
        return NotifyResult(True, order_no=form.get('out_trade_no'),
                            amount_yuan=amount, trade_no=form.get('trade_no'))

    def query_order(self, gw, order_no):
        params = {
            'act': 'order',
            'pid': self.cred(gw, 'pid'),
            'key': self._key(gw),
            'out_trade_no': order_no,
        }
        resp = _post_form('%s/api.php' % self._gateway(gw), params)
        data = resp.json()
        status = data.get('trade_status') or (data.get('data') or {}).get('trade_status')
        paid = status == 'TRADE_SUCCESS'
        amount = None
        if paid:
            amount = to_yuan(data.get('money') or (data.get('data') or {}).get('money'))
        return {'paid': paid, 'trade_no': data.get('trade_no'), 'amount_yuan': amount}


# ============================== 虎皮椒 ==============================

class HupijiaoGateway(PaymentGateway):
    """虎皮椒 V3：非空参数排序 k=v 以 & 连接，末尾直接拼接 appsecret，MD5"""
    type_id = 'hupijiao'
    label = '虎皮椒'

    DEFAULT_GATEWAY = 'https://api.xunhupay.com'

    def _gateway(self, gw):
        return (self.conf(gw, 'gateway') or self.DEFAULT_GATEWAY).strip().rstrip('/')

    def _sign(self, gw, params):
        return md5_hex(sorted_sign_content(params, exclude=('hash',))
                       + self.cred(gw, 'appsecret'))

    def create_order(self, gw, order_no, amount_yuan, subject, notify_url,
                     return_url):
        payload = {
            'version': '1.1',
            'appid': self.cred(gw, 'appid'),
            'trade_order_id': order_no,
            'total_fee': str(Decimal(amount_yuan).quantize(Decimal('0.01'))),
            'title': subject or ('充值订单 %s' % order_no),
            'time': str(int(time.time())),
            'notify_url': notify_url,
            'return_url': return_url,
            'nonce_str': os.urandom(16).hex(),
        }
        payload['hash'] = self._sign(gw, payload)
        resp = _post_json('%s/payment/do.html' % self._gateway(gw), payload)
        data = resp.json()
        if data.get('errcode') != 0:
            raise RuntimeError('hupijiao failed: %s' % data.get('errmsg') or resp.text[:200])
        result = {'pay_url': data.get('url') or None}
        if data.get('url_qrcode'):
            result['qr_code'] = data['url_qrcode']
        return result

    def verify_notify(self, gw, raw_body, headers, args, form):
        sign = form.get('hash', '')
        if not sign or not hmac.compare_digest(sign, self._sign(gw, form)):
            return NotifyResult(False, reason='bad sign')
        if form.get('status') != 'OD':
            return NotifyResult(False, paid=False, reason='status not OD')
        try:
            amount = to_yuan(form.get('total_fee'))
        except ValueError:
            return NotifyResult(False, reason='bad amount')
        return NotifyResult(True, order_no=form.get('trade_order_id'),
                            amount_yuan=amount, trade_no=form.get('transaction_id'))

    def query_order(self, gw, order_no):
        payload = {
            'version': '1.1',
            'appid': self.cred(gw, 'appid'),
            'trade_order_id': order_no,
            'time': str(int(time.time())),
            'nonce_str': os.urandom(16).hex(),
        }
        payload['hash'] = self._sign(gw, payload)
        resp = _post_json('%s/payment/search.html' % self._gateway(gw), payload)
        data = resp.json()
        paid = data.get('status') == 'OD'
        amount = to_yuan(data['total_fee']) if paid and data.get('total_fee') else None
        return {'paid': paid, 'trade_no': data.get('transaction_id'), 'amount_yuan': amount}


# ============================== Stripe Checkout ==============================

class StripeGateway(PaymentGateway):
    type_id = 'stripe'
    label = 'Stripe'

    BASE = 'https://api.stripe.com'

    def _base(self, gw):
        return (self.conf(gw, 'base_url') or self.BASE).strip().rstrip('/')

    def _auth(self, gw):
        return (self.cred(gw, 'secret_key'), '')

    def create_order(self, gw, order_no, amount_yuan, subject, notify_url,
                     return_url):
        currency = (self.conf(gw, 'currency') or 'CNY').lower()
        cents = int((Decimal(amount_yuan) * 100).to_integral_value())
        data = {
            'mode': 'payment',
            'client_reference_id': order_no,
            'success_url': return_url,
            'cancel_url': return_url,
            'line_items[0][quantity]': '1',
            'line_items[0][price_data][currency]': currency,
            'line_items[0][price_data][product_data][name]': subject or ('充值订单 %s' % order_no),
            'line_items[0][price_data][unit_amount]': str(cents),
            'metadata[order_no]': order_no,
        }
        resp = requests.post(self._base(gw) + '/v1/checkout/sessions', data=data,
                             auth=self._auth(gw), timeout=TIMEOUT)
        if resp.status_code != 200 or not resp.json().get('url'):
            raise RuntimeError('stripe failed: %s' % resp.text[:200])
        return {'pay_url': resp.json()['url']}

    def verify_notify(self, gw, raw_body, headers, args, form):
        sig = headers.get('Stripe-Signature') or headers.get('stripe-signature')
        if not sig:
            return NotifyResult(False, reason='no signature header')
        elements = dict()
        for part in sig.split(','):
            if '=' in part:
                k, v = part.split('=', 1)
                elements.setdefault(k, []).append(v)
        ts_list = elements.get('t')
        v1_list = elements.get('v1')
        if not ts_list or not v1_list:
            return NotifyResult(False, reason='bad signature header')
        try:
            if abs(int(time.time()) - int(ts_list[0])) > STRIPE_NOTIFY_WINDOW:
                return NotifyResult(False, reason='timestamp expired')
        except ValueError:
            return NotifyResult(False, reason='bad timestamp')
        secret = self.cred(gw, 'webhook_secret')
        signed = '%s.%s' % (ts_list[0], raw_body)
        expected = hmac_sha256_hex(secret, signed)
        valid = any(hmac.compare_digest(expected, v) for v in v1_list)
        if not valid:
            return NotifyResult(False, reason='bad sign')
        try:
            event = json.loads(raw_body)
        except ValueError:
            return NotifyResult(False, reason='bad json')
        if event.get('type') != 'checkout.session.completed':
            return NotifyResult(False, paid=False, reason='other event')
        obj = event.get('data', {}).get('object', {})
        if obj.get('payment_status') != 'paid':
            return NotifyResult(False, paid=False, reason='not paid')
        try:
            amount = (Decimal(str(int(obj['amount_total']))) / 100).quantize(Decimal('0.01'))
        except (KeyError, InvalidOperation, TypeError):
            return NotifyResult(False, reason='bad amount')
        return NotifyResult(True, order_no=obj.get('client_reference_id'),
                            amount_yuan=amount, trade_no=obj.get('payment_intent'))

    def query_order(self, gw, order_no):
        resp = requests.get(
            self._base(gw) + '/v1/checkout/sessions',
            params={'client_reference_id': order_no},
            auth=self._auth(gw), timeout=TIMEOUT)
        items = resp.json().get('data') or []
        if not items:
            return {'paid': False, 'trade_no': None, 'amount_yuan': None}
        obj = items[0]
        paid = obj.get('payment_status') == 'paid'
        amount = None
        if paid and obj.get('amount_total') is not None:
            amount = (Decimal(str(int(obj['amount_total']))) / 100).quantize(Decimal('0.01'))
        return {'paid': paid, 'trade_no': obj.get('payment_intent'), 'amount_yuan': amount}

    def ack(self, ok):
        return '', 200 if ok else 400, {'Content-Type': 'application/json'}


# ============================== 京东收银（标准签名骨架） ==============================

class JdGateway(PaymentGateway):
    """京东收银/京东支付适配器骨架。

    依据京东支付开放平台公开信息：签名类型固定 SHA-256（RSA 证书体系），
    商户用私钥对业务参数签名(signData)，京东用商户公钥/证书验签；
    回调同理。各商户的具体字段名以京东支付商户后台/对接 SDK 文档为准，
    填入真实凭证并按下单字段联调后即可启用（本系统不做资金联网验证）。
    """
    type_id = 'jd'
    label = '京东收银'
    supports_query = False

    def _gateway(self, gw):
        return (self.conf(gw, 'gateway') or '').strip()

    def _build_sign(self, gw, params):
        return rsa_sign_sha256(self.cred(gw, 'merchant_private_key'),
                               sorted_sign_content(params, exclude=('signData', 'signType')))

    def create_order(self, gw, order_no, amount_yuan, subject, notify_url,
                     return_url):
        gateway = self._gateway(gw)
        if not gateway:
            raise RuntimeError('jd gateway url not configured')
        params = {
            'merchantNo': self.cred(gw, 'merchant_no'),
            'outTradeNo': order_no,
            'amount': str(Decimal(amount_yuan).quantize(Decimal('0.01'))),
            'subject': subject or ('充值订单 %s' % order_no),
            'notifyUrl': notify_url,
            'returnUrl': return_url,
            'version': 'V2.0',
        }
        params['signType'] = 'SHA-256'
        params['signData'] = self._build_sign(gw, params)
        return {'pay_url': '%s?%s' % (gateway, urllib_parse.urlencode(params))}

    def verify_notify(self, gw, raw_body, headers, args, form):
        raw = (raw_body or '').strip()
        try:
            payload = json.loads(raw) if raw.startswith('{') else dict(form)
        except ValueError:
            return NotifyResult(False, reason='bad payload')
        if not isinstance(payload, dict):
            return NotifyResult(False, reason='bad payload')
        sign = payload.get('signData') or ''
        if not sign:
            return NotifyResult(False, reason='no signData')
        message = sorted_sign_content(payload, exclude=('signData', 'signType'))
        if not rsa_verify_sha256(self.cred(gw, 'jd_public_key'), sign, message):
            return NotifyResult(False, reason='bad sign')
        status = str(payload.get('tradeState') or payload.get('status') or '')
        if status not in ('SUCCESS', '1', 'TRADE_SUCCESS'):
            return NotifyResult(False, paid=False, reason='not success')
        order_no = payload.get('outTradeNo') or payload.get('orderId')
        trade_no = payload.get('tradeNo') or payload.get('transactionId')
        try:
            amount = to_yuan(payload.get('amount') or payload.get('totalAmount'))
        except ValueError:
            return NotifyResult(False, reason='bad amount')
        return NotifyResult(True, order_no=order_no, amount_yuan=amount, trade_no=trade_no)

    def ack(self, ok):
        body = json.dumps({'code': '0' if ok else '1',
                           'msg': 'success' if ok else 'verify failed'},
                          ensure_ascii=False)
        return body, (200 if ok else 400), \
            {'Content-Type': 'application/json; charset=utf-8'}


# ============================== 注册表与渠道元数据 ==============================

REGISTRY = {
    StaticQrcodeGateway.type_id: StaticQrcodeGateway,
    AlipayF2FGateway.type_id: AlipayF2FGateway,
    WechatNativeGateway.type_id: WechatNativeGateway,
    EpayGateway.type_id: EpayGateway,
    HupijiaoGateway.type_id: HupijiaoGateway,
    StripeGateway.type_id: StripeGateway,
    JdGateway.type_id: JdGateway,
}
_instances = {}


def get_gateway(type_id):
    if type_id not in REGISTRY:
        return None
    if type_id not in _instances:
        _instances[type_id] = REGISTRY[type_id]()
    return _instances[type_id]


# 渠道类型展示元数据（供后台表单/图标渲染与凭证字段渲染）
# field kind: text / secret / textarea_secret / url
# field 扩展属性：group(分组标题) / required(默认true) / placeholder / help(官方获取指引)
# 说明：各字段 key 必须与对应网关适配器实际读取的凭证/配置键保持一致，勿随意改名。
CHANNEL_SPEC = {
    'static_qrcode': {
        'label': '个人收款码',
        'icon': 'fas fa-qrcode',
        'supports_notify': False,
        'supports_query': False,
        'hint': '沿用现有机制：用户扫描管理员上传的收款码转账，始终需要人工审核。',
        'credential_fields': [],
        'config_fields': [
            {'key': 'qr_brand', 'label': '二维码类型', 'kind': 'select', 'group': None,
             'required': True, 'default': 'qrcode',
             'options': [
                 {'value': 'alipay', 'label': '支付宝收款码'},
                 {'value': 'wechat', 'label': '微信收款码'},
                 {'value': 'qqpay', 'label': 'QQ 钱包收款码'},
                 {'value': 'bank', 'label': '银行卡收款码'},
                 {'value': 'qrcode', 'label': '通用收款码（无法确定类型）'},
             ],
             'help': '按收款码所属平台选择，用户端与订单记录将显示对应官方 Logo；无法确定时选「通用收款码」。'},
        ],
    },
    'alipay_f2f': {
        'label': '支付宝当面付',
        'icon': 'fab fa-alipay',
        'supports_notify': True,
        'supports_query': True,
        'hint': '支付宝开放平台「电脑网站/当面付（alipay.trade.precreate）」产品。需企业或个体工商户资质；'
                '签名方式固定 RSA2，异步通知地址（notify_url）由系统按下单请求自动上报。',
        'credential_fields': [
            {'key': 'app_id', 'label': '应用 APPID', 'kind': 'text', 'group': '一、商户应用信息',
             'required': True, 'placeholder': '2021000000000000',
             'help': '支付宝开放平台 → 控制台 → 我的应用 → 应用详情中的 APPID（16 位数字）。'},
            {'key': 'app_private_key', 'label': '应用私钥', 'kind': 'textarea_secret', 'group': '二、接口加签参数（RSA2）',
             'required': True, 'placeholder': '-----BEGIN RSA PRIVATE KEY-----\n......\n-----END RSA PRIVATE KEY-----',
             'help': '使用「支付宝开放平台密钥工具」生成的 RSA2 应用私钥，PKCS8/PEM 格式；仅需粘贴密钥内容，系统加密存储。'},
            {'key': 'alipay_public_key', 'label': '支付宝公钥', 'kind': 'textarea_secret', 'group': '二、接口加签参数（RSA2）',
             'required': True, 'placeholder': '-----BEGIN PUBLIC KEY-----\n......\n-----END PUBLIC KEY-----',
             'help': '应用详情「开发信息 → 接口加签方式」中上传/保存 CSR 后由支付宝平台分配的支付宝公钥（PEM），用于异步回调验签。'},
        ],
        'config_fields': [
            {'key': 'gateway', 'label': '支付宝网关端点', 'kind': 'url', 'group': '三、接口端点',
             'required': True, 'default': 'https://openapi.alipay.com/gateway.do',
             'help': '正式环境：https://openapi.alipay.com/gateway.do；沙箱环境：https://openapi-sandbox.dl.alipaydev.com/gateway.do'},
        ],
    },
    'wechat_native': {
        'label': '微信Native支付',
        'icon': 'fab fa-weixin',
        'supports_notify': True,
        'supports_query': True,
        'hint': '微信支付 APIv3「Native 下单（/v3/pay/transactions/native）」。商户私钥、证书序列号与 APIv3 密钥均在微信支付商户平台获取；'
                'notify_url 随下单请求上报，必须为公网可达的 HTTPS 地址。',
        'credential_fields': [
            {'key': 'appid', 'label': '应用 AppID', 'kind': 'text', 'group': '一、商户身份信息',
             'required': True, 'placeholder': 'wx1234567890abcdef',
             'help': '公众号 / 小程序 / 移动应用 / 企业微信互通 的 AppID，且该应用已与商户号绑定（产品中心 → AppID 账号管理）。'},
            {'key': 'mchid', 'label': '微信支付商户号', 'kind': 'text', 'group': '一、商户身份信息',
             'required': True, 'placeholder': '1900000000',
             'help': '微信支付商户平台 → 账户中心 → 商户信息 中的商户号（10 位数字）。'},
            {'key': 'merchant_serial', 'label': '商户证书序列号', 'kind': 'text', 'group': '二、APIv3 证书与密钥',
             'required': True, 'placeholder': '证书序列号（40 位十六进制）',
             'help': '商户平台「账户中心 → API 安全 → API 证书」中申请的 API 证书序列号，即 apiclient_cert.pem 的序列号。'},
            {'key': 'merchant_private_key', 'label': '商户 API 私钥', 'kind': 'textarea_secret', 'group': '二、APIv3 证书与密钥',
             'required': True, 'placeholder': '-----BEGIN PRIVATE KEY-----\n......\n-----END PRIVATE KEY-----',
             'help': '下载 API 证书得到的 apiclient_key.pem 文件内容（PEM），用于请求签名 Authorization 头。'},
            {'key': 'apiv3_key', 'label': 'APIv3 密钥', 'kind': 'secret', 'group': '二、APIv3 证书与密钥',
             'required': True, 'placeholder': '32 位字母数字字符串',
             'help': '商户平台「API 安全 → APIv3 密钥」中设置的 32 字节密钥，用于回调解密 AES-GCM 报文。'},
            {'key': 'platform_cert', 'label': '微信支付平台证书（可选）', 'kind': 'textarea_secret', 'group': '二、APIv3 证书与密钥',
             'required': False, 'placeholder': '-----BEGIN CERTIFICATE-----\n......\n-----END CERTIFICATE-----',
             'help': '留空时系统自动调用 /v3/certificates 下载并用 APIv3 密钥解密缓存；如服务器无法出网，可手动粘贴平台证书 PEM。'},
        ],
        'config_fields': [
            {'key': 'base_url', 'label': '微信支付接口基址', 'kind': 'url', 'group': '三、接口端点',
             'required': True, 'default': 'https://api.mch.weixin.qq.com',
             'help': '正式环境固定为 https://api.mch.weixin.qq.com，通常无需修改。'},
        ],
    },
    'epay': {
        'label': '易支付（彩虹/兼容站点）',
        'icon': 'fas fa-wallet',
        'supports_notify': True,
        'supports_query': True,
        'hint': '兼容彩虹易支付通用协议（submit.php 下单 / api.php 查单，MD5 签名）的聚合站点。'
                '需在易支付商户后台登记下方「异步回调地址」，同步跳转地址（return_url）由系统自动传参。',
        'credential_fields': [
            {'key': 'pid', 'label': '商户 ID（PID）', 'kind': 'text', 'group': '一、商户参数',
             'required': True, 'placeholder': '10000',
             'help': '易支付站点商户后台 → 商户ID（pid）。'},
            {'key': 'key', 'label': '商户密钥（KEY）', 'kind': 'secret', 'group': '一、商户参数',
             'required': True, 'placeholder': '商户通信密钥',
             'help': '商户密钥（key），用于 MD5 签名校验；泄露后请立即在易支付后台重置并同步更新。'},
        ],
        'config_fields': [
            {'key': 'gateway', 'label': '易支付站点端点', 'kind': 'url', 'group': '二、接口端点',
             'required': True, 'default': '', 'placeholder': 'https://pay.example.com',
             'help': '易支付站点根地址（不要带 /submit.php 后缀），系统自动拼接下单 /submit.php、查单 /api.php。'},
            {'key': 'pay_type', 'label': '默认支付方式编码', 'kind': 'select', 'group': '二、接口端点',
             'required': True, 'default': 'alipay',
             'options': [
                 {'value': 'alipay', 'label': '支付宝（alipay）'},
                 {'value': 'wxpay', 'label': '微信支付（wxpay）'},
                 {'value': 'qqpay', 'label': 'QQ 钱包（qqpay）'},
                 {'value': 'bank', 'label': '网银支付（bank）'},
             ],
             'help': '由站点支持情况决定，常见值：alipay（支付宝）、wxpay（微信）、qqpay（QQ 钱包）、bank（网银）；以站点文档为准。'},
        ],
    },
    'hupijiao': {
        'label': '虎皮椒',
        'icon': 'fas fa-leaf',
        'supports_notify': True,
        'supports_query': True,
        'hint': '虎皮椒（ Xunhupay ）个人支付 V3 接口：/payment/do.html 下单、/payment/search.html 查单，MD5 签名。'
                '需在虎皮椒后台应用配置中登记下方异步回调地址（notify_url）。',
        'credential_fields': [
            {'key': 'appid', 'label': '应用 AppID', 'kind': 'text', 'group': '一、商户应用参数',
             'required': True, 'placeholder': '虎皮椒应用 AppID',
             'help': '登录虎皮椒后台 → 应用管理 → 创建/查看应用 获取的 AppID。'},
            {'key': 'appsecret', 'label': '应用密钥 AppSecret', 'kind': 'secret', 'group': '一、商户应用参数',
             'required': True, 'placeholder': '应用密钥',
             'help': '与 AppID 配对的 AppSecret，用于下单与回调 MD5 签名。'},
        ],
        'config_fields': [
            {'key': 'gateway', 'label': '虎皮椒网关端点', 'kind': 'url', 'group': '二、接口端点',
             'required': True, 'default': 'https://api.xunhupay.com',
             'help': '官方默认网关 https://api.xunhupay.com；使用兼容/自营网关时按服务商提供地址填写。'},
        ],
    },
    'stripe': {
        'label': 'Stripe',
        'icon': 'fab fa-stripe',
        'supports_notify': True,
        'supports_query': True,
        'hint': 'Stripe Checkout 托管收银台。Webhook 端点必须订阅 checkout.session.completed 事件，'
                '端点地址填下方「异步回调地址」，签名密钥（whsec_）用于回调验签。',
        'credential_fields': [
            {'key': 'secret_key', 'label': 'Secret Key（密钥）', 'kind': 'secret', 'group': '一、API 密钥',
             'required': True, 'placeholder': 'sk_live_xxxxxxxxxxxx（测试用 sk_test_）',
             'help': 'Stripe Dashboard → Developers → API keys → Secret key；正式环境请使用 sk_live_ 开头密钥。'},
            {'key': 'webhook_secret', 'label': 'Webhook 签名密钥', 'kind': 'secret', 'group': '一、API 密钥',
             'required': True, 'placeholder': 'whsec_xxxxxxxxxxxx',
             'help': 'Dashboard → Developers → Webhooks 添加端点（地址为下方回调地址、勾选 checkout.session.completed）后获得的 Signing secret。'},
        ],
        'config_fields': [
            {'key': 'base_url', 'label': 'API 端点', 'kind': 'url', 'group': '二、接口端点',
             'required': True, 'default': 'https://api.stripe.com',
             'help': '官方端点 https://api.stripe.com，通常无需修改。'},
            {'key': 'currency', 'label': '结算币种', 'kind': 'text', 'group': '二、接口端点',
             'required': True, 'default': 'cny',
             'help': '三位 ISO 4217 币种代码，如 cny / usd / hkd，需与 Stripe 账户开通币种一致。'},
        ],
    },
    'jd': {
        'label': '京东收银',
        'icon': 'fas fa-shopping-bag',
        'supports_notify': True,
        'supports_query': False,
        'hint': '京东支付（网银在线）收银台，签名类型 SHA-256/RSA。以下参数与京东支付商户后台/对接文档一一对应，'
                '填入真实凭证并完成真机回调联调后启用；回调地址需在商户后台登记。',
        'credential_fields': [
            {'key': 'merchant_no', 'label': '京东商户号', 'kind': 'text', 'group': '一、商户参数',
             'required': True, 'placeholder': '商户号（merchantNo）',
             'help': '京东支付/网银在线商户后台分配的商户号。'},
            {'key': 'merchant_private_key', 'label': '商户私钥', 'kind': 'textarea_secret', 'group': '二、签名密钥（SHA-256/RSA）',
             'required': True, 'placeholder': '-----BEGIN RSA PRIVATE KEY-----\n......\n-----END RSA PRIVATE KEY-----',
             'help': '商户自行生成并在商户后台登记公钥后对应的 RSA 私钥（PEM），用于下单参数签名 signData。'},
            {'key': 'jd_public_key', 'label': '京东支付平台公钥', 'kind': 'textarea_secret', 'group': '二、签名密钥（SHA-256/RSA）',
             'required': True, 'placeholder': '-----BEGIN PUBLIC KEY-----\n......\n-----END PUBLIC KEY-----',
             'help': '京东支付平台公钥/平台证书（PEM），用于验签异步回调报文。'},
        ],
        'config_fields': [
            {'key': 'gateway', 'label': '收银台下单端点', 'kind': 'url', 'group': '三、接口端点',
             'required': True, 'default': '', 'placeholder': 'https://[京东商户后台分配的收银台地址]',
             'help': '京东支付商户后台/对接文档提供的收银台下单地址，系统将签名参数拼接后跳转。'},
        ],
    },
}
