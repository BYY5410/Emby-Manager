#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
管理员密码重置脚本（紧急维护工具，请妥善保管并限制访问）

════════════════════════════════════════════════════════════════════════
  支持：
    ✓ 查询系统内所有现存管理员账户（含精确列表）
    ✓ 对已存在的管理员执行密码重置（任意长度、任意合法字符）
    ✓ 修改管理员用户名（任意长度、任意可打印字符）
    ✓ 恢复初始设置（用户名 admin / 密码 123 / 强制改密）
    ✗ 不支持新建管理员账户
════════════════════════════════════════════════════════════════════════

设计：
  - 精确按用户名定位目标管理员（默认 admin；CLI 用 -a 指定）
  - 危险操作二次确认（CLI 用 -y 跳过）
  - 所有硬约束公开暴露：PASSWORD_MAX_INPUT_HARD=65536, USERNAME_DB_MAX=50
  - 密码经 scrypt 单向哈希，重置后自动置 must_change_password=1
  - SQLAlchemy 参数化查询，无 SQL 注入风险
  - 日志同时写入控制台和 reset_admin.log（可审计）

两种入口：
  python reset_admin_password.py                       # 无参数 → 交互式主菜单
  python reset_admin_password.py NewPw -a admin -y     # CLI 模式（自动化调用）
  python reset_admin_password.py -l                    # CLI 列出所有管理员
  python reset_admin_password.py -u newadmin -y        # CLI 仅改用户名
  python reset_admin_password.py --restore-initial -y  # CLI 恢复初始设置
"""

import logging
import os
import sys
import getpass
import argparse
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from werkzeug.security import generate_password_hash
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from database.models import User, UserRole

# ==================== 数据库配置 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database', 'emby_manager.db')
DB_URL = 'sqlite:///' + DB_PATH.replace('\\', '/')
SessionLocal = sessionmaker(bind=create_engine(DB_URL))

# ==================== 公开硬约束 ====================
USERNAME_DB_MAX = 50           # User.username VARCHAR(50)
PASSWORD_HASH_DB_MAX = 255     # scrypt 哈希固定约 90 字符，永远安全
PASSWORD_MAX_INPUT_WARN = 4096
PASSWORD_MAX_INPUT_HARD = 65536
INITIAL_USERNAME = 'admin'
INITIAL_PASSWORD = '123'

# ==================== 日志 ====================
def _setup_logging():
    log_path = os.path.join(BASE_DIR, 'reset_admin.log')
    fmt = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    logger = logging.getLogger('reset_admin')
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt); sh.setLevel(logging.INFO)
    logger.addHandler(sh)

    try:
        fh = logging.FileHandler(log_path, encoding='utf-8')
        fh.setFormatter(fmt); fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)
    except Exception as e:
        sys.stderr.write(f'[WARN] 日志文件 {log_path} 不可写: {e}\n')
    return logger


log = _setup_logging()


# ==================== 输入校验 ====================
_BAD_USERNAME_CHARS = set(chr(c) for c in range(0x00, 0x20))  # 0x00-0x1F
_BAD_USERNAME_CHARS.add('\x7f')  # DEL


def validate_username(name: str, *, db_max: int = USERNAME_DB_MAX) -> tuple[bool, str]:
    if name is None or len(name) == 0 or not name.strip():
        return False, '用户名不能为空或纯空白'
    if len(name) > db_max:
        return False, f'用户名长度 {len(name)} 超过数据库列上限 {db_max}'
    bad = [c for c in name if c in _BAD_USERNAME_CHARS]
    if bad:
        return False, f'用户名包含非法控制字符（\\x{ord(bad[0]):02x}）'
    return True, ''


def validate_password(pw: str) -> tuple[bool, str]:
    if pw is None or len(pw) == 0:
        return False, '密码不能为空'
    if len(pw) > PASSWORD_MAX_INPUT_HARD:
        return False, f'密码长度 {len(pw)} 超过硬上限 {PASSWORD_MAX_INPUT_HARD}'
    return True, ''


# ==================== 数据库工具 ====================
def open_db():
    if not os.path.exists(DB_PATH):
        return None, f'数据库文件不存在: {DB_PATH}'
    try:
        return SessionLocal(), None
    except SQLAlchemyError as e:
        return None, f'数据库连接失败: {e}'


def list_admins(db) -> list:
    return db.query(User).filter(User.role == UserRole.ADMIN).order_by(User.id.asc()).all()


def find_admin(db, admin_name: str):
    admin = db.query(User).filter(User.role == UserRole.ADMIN, User.username == admin_name).first()
    if admin is None:
        admins = list_admins(db)
        hint = '当前系统管理员列表：' + (
            '\n  ' + '、'.join(f'"{a.username}"' for a in admins) if admins
            else '\n  （空）本脚本不支持新建管理员账户。'
        )
        return None, f'未找到管理员 "{admin_name}"。{hint}'
    return admin, ''


def format_admin_table(admins: list) -> str:
    if not admins:
        return '  （空）本脚本不支持新建管理员账户。'
    lines = ['  {:<4} {:<6} {:<26} {:<10} {:<14}'.format('#', 'ID', '用户名', '状态', '强制改密')]
    lines.append('  ' + '-' * 62)
    for i, a in enumerate(admins, 1):
        uname = a.username[:24] + ('…' if len(a.username) > 24 else '')
        status = '启用' if a.is_active else '禁用'
        fcm = '是' if a.must_change_password else '否'
        lines.append('  {:<4} {:<6} {:<26} {:<10} {:<14}'.format(i, a.id, uname, status, fcm))
    return '\n'.join(lines)


# ==================== 业务函数 ====================
def do_change_username(db, admin, new_username: str) -> tuple[bool, str]:
    ok, err = validate_username(new_username)
    if not ok:
        return False, f'用户名校验失败: {err}'
    old = admin.username
    if new_username == old:
        return True, f'新用户名与当前用户名相同（"{old}"），无变更'
    dup = db.query(User.id).filter(User.username == new_username).first()
    if dup and dup[0] != admin.id:
        return False, f'用户名 "{new_username}" 已被其他账户（id={dup[0]}）占用'
    admin.username = new_username
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        return False, f'数据写入冲突: {e.orig if e.orig else e}'
    except SQLAlchemyError as e:
        db.rollback(); return False, f'数据库错误: {e}'
    log.info(f'用户名变更: id={admin.id}, "{old}" → "{new_username}"')
    return True, f'管理员（id={admin.id}）用户名已由 "{old}" 改为 "{new_username}"'


def do_reset_password(db, admin, new_password: str) -> tuple[bool, str]:
    ok, err = validate_password(new_password)
    if not ok:
        return False, f'密码校验失败: {err}'
    if len(new_password) > PASSWORD_MAX_INPUT_WARN:
        log.warning(f'密码长度 {len(new_password)} 字符，scrypt 哈希会显著变慢')
    hashed = generate_password_hash(new_password)
    if len(hashed) > PASSWORD_HASH_DB_MAX:
        return False, f'哈希长度 {len(hashed)} 超过数据库列上限 {PASSWORD_HASH_DB_MAX}'
    admin.password_hash = hashed
    admin.must_change_password = True
    try:
        db.commit()
    except SQLAlchemyError as e:
        db.rollback(); return False, f'数据库错误: {e}'
    log.info(f'密码重置: id={admin.id}, username="{admin.username}", scrypt 哈希 {len(hashed)} 字符, must_change=True')
    return True, (f'管理员（id={admin.id}）密码已重置'
                  f'（scrypt 哈希 {len(hashed)} 字符）；'
                  f'\n  已自动标记 must_change_password=True，下次登录强制修改。')


def do_restore_initial(db, admin) -> tuple[bool, str]:
    """恢复到初始设置：用户名 admin / 密码 123 / must_change=True。"""
    dup = db.query(User.id).filter(User.username == INITIAL_USERNAME).first()
    if dup and dup[0] != admin.id:
        return False, f'无法恢复初始用户名："{INITIAL_USERNAME}" 已被其他账户（id={dup[0]}）占用'
    admin.username = INITIAL_USERNAME
    admin.password_hash = generate_password_hash(INITIAL_PASSWORD)
    admin.must_change_password = True
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback(); return False, f'数据写入冲突: {e.orig if e.orig else e}'
    except SQLAlchemyError as e:
        db.rollback(); return False, f'数据库错误: {e}'
    log.warning(f'恢复初始设置: id={admin.id} → username="{INITIAL_USERNAME}", 密码="{INITIAL_PASSWORD}", must_change=True')
    return True, (f'已恢复初始设置：'
                  f'\n  用户名 → "{INITIAL_USERNAME}"'
                  f'\n  密码 → "{INITIAL_PASSWORD}"'
                  f'\n  must_change_password → True（下次登录强制修改）')


# ==================== 通用交互 ====================
def _hidden_input(prompt: str) -> str:
    try:
        if sys.stdin.isatty():
            return getpass.getpass(prompt)
    except Exception:
        pass
    return input(prompt)


def _yes_no(prompt: str, default_yes: bool = False) -> bool:
    suffix = ' [Y/n]' if default_yes else ' [y/N]'
    while True:
        try:
            ans = input(prompt + suffix + ': ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if not ans: return default_yes
        if ans in ('y', 'yes', '是'): return True
        if ans in ('n', 'no', '否'): return False
        print('请输入 y 或 n')


def prompt_admin_pick(admins: list) -> str:
    count = len(admins)
    if count == 0:
        return ''
    print('\n系统内现存的管理员账户：')
    print(format_admin_table(admins))
    while True:
        try:
            raw = input(f'请输入目标管理员的序号（1-{count}）或直接输入用户名: ').strip()
        except (EOFError, KeyboardInterrupt):
            print(); return ''
        if not raw:
            print('输入不能为空'); continue
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= count:
                return admins[idx - 1].username
            print(f'序号 {idx} 超出范围（1-{count}），请重新输入'); continue
        return raw


# ==================== 交互式主菜单 ====================
MENU_BANNER = r"""
╔══════════════════════════════════════════════════════════════════╗
║              管理员账户恢复工具 - 主菜单                           ║
╠══════════════════════════════════════════════════════════════════╣
║  支持：                                                           ║
║    ✓ 查询所有现存管理员账户                                        ║
║    ✓ 密码重置（任意长度、任意字符）                                 ║
║    ✓ 修改用户名（任意长度、任意可打印字符）                         ║
║    ✓ 恢复初始设置（admin / 123 / 强制改密）                        ║
║  不支持：                                                         ║
║    ✗ 新建管理员账户                                                ║
║  所有操作自动标记 "下次登录强制修改密码"                            ║
╠══════════════════════════════════════════════════════════════════╣
║  1. 重置管理员密码（推荐，自动列出账户供选择）                     ║
║  2. 修改管理员用户名（自动列出账户供选择）                         ║
║  3. 恢复初始设置（自动列出账户供选择）                             ║
║  4. 仅查看当前所有管理员账户                                        ║
║  5. 退出                                                           ║
╚══════════════════════════════════════════════════════════════════╝
"""


def interactive_mode():
    log.info('进入交互式主菜单')

    while True:
        print(MENU_BANNER)

        choice = ''
        while True:
            try:
                choice = input('请选择操作 [1-5]: ').strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print('用户中断，程序退出'); log.info('用户 Ctrl-C / EOF 退出'); sys.exit(0)
            if choice in ('1', '2', '3', '4', '5'):
                break
            print(f'无效输入 "{choice}"，请输入数字 1-5')

        if choice == '5':
            print('已退出管理员账户恢复工具'); log.info('用户选择 5 退出'); sys.exit(0)

        db, err = open_db()
        if db is None:
            print(f'\n[错误] {err}')
            input('按回车返回主菜单...'); continue

        try:
            admins = list_admins(db)
            if not admins:
                print('\n[错误] 当前系统内没有任何管理员账户')
                print('本脚本不支持新建管理员账户，请先通过系统其他方式创建。')
                input('按回车返回主菜单...'); continue

            if choice == '4':
                print('\n当前系统内所有管理员账户：')
                print(format_admin_table(admins))
                input('\n按回车返回主菜单...'); continue

            # choice 1/2/3 — 都需要选目标管理员
            print()
            target_name = prompt_admin_pick(admins)
            if not target_name:
                print('\n已取消')
                input('按回车返回主菜单...'); continue

            admin, err = find_admin(db, target_name)
            if admin is None:
                print(f'\n[错误] {err}')
                input('按回车返回主菜单...'); continue

            print(f'\n目标: id={admin.id}, 用户名="{admin.username}", 状态={"启用" if admin.is_active else "禁用"}')

            # 二次确认（除了改密码都很危险）
            print()
            if not _yes_no('确认执行此操作？该操作将直接修改生产数据库', default_yes=False):
                print('已取消'); input('按回车返回主菜单...'); continue

            if choice == '1':
                print('\n即将重置管理员密码（支持任意长度、任意可打印字符；硬上限 %d 字符）' % PASSWORD_MAX_INPUT_HARD)
                while True:
                    pw1 = _hidden_input('请输入新密码: ')
                    ok, err = validate_password(pw1)
                    if not ok:
                        print(f'  密码校验失败: {err}，请重新输入'); continue
                    pw2 = _hidden_input('请再次输入新密码: ')
                    if pw1 != pw2:
                        print('  两次输入不一致，请重新输入'); continue
                    if len(pw1) > PASSWORD_MAX_INPUT_WARN:
                        print(f'  [警告] 密码长度 {len(pw1)} 字符，scrypt 哈希会显著变慢')
                        if not _yes_no('  仍然继续？', default_yes=False):
                            print('  已取消'); input('按回车返回主菜单...'); break
                    break
                else:
                    ok, msg = do_reset_password(db, admin, pw1)
                    print(); print('[成功] ' + msg.replace('\n', '\n  ')) if ok else print('[失败] ' + msg)

            elif choice == '2':
                while True:
                    new_name = input('请输入新的管理员用户名（任意可打印字符，硬上限 %d 字符）: ' % USERNAME_DB_MAX).strip()
                    ok, err = validate_username(new_name)
                    if not ok:
                        print(f'  用户名校验失败: {err}，请重新输入'); continue
                    break
                ok, msg = do_change_username(db, admin, new_name)
                print(); print('[成功] ' + msg) if ok else print('[失败] ' + msg)

            elif choice == '3':
                print(f'\n即将执行恢复初始设置：')
                print(f'  用户名 → "{INITIAL_USERNAME}"')
                print(f'  密码   → "{INITIAL_PASSWORD}"')
                print(f'  must_change_password → True（下次登录强制修改）')
                if not _yes_no('确认继续？这将覆盖当前用户名和密码', default_yes=False):
                    print('已取消'); input('按回车返回主菜单...'); continue
                ok, msg = do_restore_initial(db, admin)
                print(); print('[成功] ' + msg.replace('\n', '\n  ')) if ok else print('[失败] ' + msg)

        except (EOFError, KeyboardInterrupt):
            print(); print('操作已取消')
        except Exception as e:
            log.error(f'未预期错误: {e}'); traceback.print_exc()
            print(f'[内部错误] {e}，详情见 reset_admin.log')
        finally:
            try: db.close()
            except Exception: pass

        print(); input('按回车返回主菜单...')


# ==================== CLI 模式 ====================
def cli_mode(argv=None):
    parser = argparse.ArgumentParser(
        description='管理员账户恢复：密码重置 / 改用户名 / 恢复初始设置（仅对已存在的管理员账户）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '示例:\n'
            '  python reset_admin_password.py -l                          # 列出所有管理员\n'
            '  python reset_admin_password.py NewPass123 -y               # 重置默认管理员密码\n'
            '  python reset_admin_password.py NewPass123 -a alice -y       # 重置指定管理员密码\n'
            '  python reset_admin_password.py -u newadmin -y              # 仅修改管理员用户名\n'
            '  python reset_admin_password.py -u newadmin NewPass123 -y   # 同时改用户名 + 重置密码\n'
            '  python reset_admin_password.py --restore-initial -y        # 恢复初始设置\n\n'
            '限制：本工具不支持新建管理员账户。'
        )
    )
    parser.add_argument('-l', '--list', action='store_true', help='列出所有管理员账户')
    parser.add_argument('password', nargs='?', help='新密码（不传则交互输入）')
    parser.add_argument('-u', '--username', dest='username', help=f'新用户名（硬上限 {USERNAME_DB_MAX} 字符）')
    parser.add_argument('-a', '--admin', dest='admin_name', default='admin',
                        help='目标管理员当前用户名（默认 admin；多管理员时务必精确指定）')
    parser.add_argument('-y', '--yes', action='store_true', help='跳过交互确认（自动化场景）')
    parser.add_argument('--restore-initial', action='store_true', help='恢复初始设置（admin/123/强制改密）')
    args = parser.parse_args(argv)

    db, err = open_db()
    if db is None:
        log.error(err); print(f'错误: {err}'); sys.exit(1)

    # 1) 仅列账户
    if args.list and not args.password and not args.username and not args.restore_initial:
        admins = list_admins(db)
        if not admins:
            print('当前系统内没有任何管理员账户')
            print('本工具不支持新建管理员账户。')
        else:
            print('系统内现存的管理员账户：')
            print(format_admin_table(admins))
        db.close(); sys.exit(0)

    # 2) 收集操作
    actions = []
    new_password = args.password
    new_username = args.username

    if args.restore_initial:
        actions.append('恢复初始设置')
    else:
        if new_username:
            actions.append(f'改用户名 → "{new_username}"')
        if new_password:
            actions.append('重置密码')
        if not actions:
            actions.append('重置密码（交互输入）')

    # 3) 密码前置校验
    if new_password:
        ok, err = validate_password(new_password)
        if not ok:
            log.error(f'密码校验失败: {err}'); print(f'错误: {err}'); sys.exit(2)
        if len(new_password) > PASSWORD_MAX_INPUT_WARN:
            log.warning(f'密码长度 {len(new_password)} 字符，scrypt 哈希会显著变慢')

    if new_username:
        ok, err = validate_username(new_username)
        if not ok:
            log.error(f'新用户名校验失败: {err}'); print(f'错误: {err}'); sys.exit(2)

    # 4) 密码交互输入（CLI 仅密码但没给）
    if not args.restore_initial and not new_password and args.username:
        pass  # 仅改用户名
    elif not new_password and not args.restore_initial:
        try:
            pw1 = _hidden_input('请输入新的管理员密码（直接回车取消）: ')
        except (EOFError, KeyboardInterrupt):
            print(); log.info('用户中断'); sys.exit(0)
        if not pw1:
            log.info('用户取消密码输入'); sys.exit(0)
        try: pw2 = _hidden_input('请再次输入新密码: ')
        except (EOFError, KeyboardInterrupt): print(); sys.exit(0)
        if pw1 != pw2: log.error('两次密码不一致'); sys.exit(2)
        new_password = pw1

    # 5) 找目标管理员
    admin, err = find_admin(db, args.admin_name)
    if admin is None:
        log.error(err); print(f'错误: {err}'); sys.exit(1)

    log.info('=' * 60); log.info(f'CLI 模式: target="{admin.username}", actions={actions}')

    # 6) 二次确认
    if not args.yes:
        print('=' * 60)
        print('  警告：此操作将直接修改生产数据库，请确认目标账户无误')
        print('=' * 60)
        print(f'  目标: id={admin.id}, 用户名="{admin.username}"')
        for a in actions: print(f'  操作: {a}')
        print()
        try: ans = input('确认执行？输入 yes 继续: ').strip()
        except (EOFError, KeyboardInterrupt): print(); log.info('用户中断'); sys.exit(0)
        if ans != 'yes':
            log.info(f'用户输入 "{ans}"，已取消'); sys.exit(0)

    # 7) 执行
    results = []
    try:
        if args.restore_initial:
            results.append(do_restore_initial(db, admin))
        else:
            if new_username:
                ok, msg = do_change_username(db, admin, new_username)
                results.append((ok, msg))
                if ok and new_password:
                    refreshed, _ = find_admin(db, new_username)
                    if refreshed is not None:
                        admin = refreshed
            if new_password:
                results.append(do_reset_password(db, admin, new_password))
    except SystemExit:
        raise
    except Exception as e:
        log.error(f'未预期错误: {e}'); traceback.print_exc(); sys.exit(1)
    finally:
        try: db.close()
        except Exception: pass

    # 8) 汇总
    print('=' * 60)
    all_ok = all(r[0] for r in results)
    if all_ok:
        print('操作成功完成！')
        for _, msg in results:
            for line in msg.split('\n'):
                print('  ' + line)
    else:
        print('部分操作失败:')
        for ok, msg in results:
            mark = '✓' if ok else '✗'
            print(f'  {mark} {msg}')
    print('=' * 60)
    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    if len(sys.argv) <= 1:
        interactive_mode()
    else:
        cli_mode()
