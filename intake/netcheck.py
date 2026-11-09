"""待ち受けが localhost だけかを確かめる（依頼文 §7：段1と段5で確認）。

  python -m intake netcheck   … intake を動かしている Python プロセスの LISTEN ソケットを全部出し、
                                 127.0.0.1 / ::1 以外があれば NG を返す。
psutil が必要（pip install psutil）。
"""

from __future__ import annotations

import os
import sys

LOOPBACK = ("127.0.0.1", "::1")


def listening_sockets() -> list[dict]:
    import psutil  # noqa: PLC0415

    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "intake" not in cmd or p.pid == os.getpid():
                continue
            for c in p.net_connections(kind="inet"):
                if c.status == psutil.CONN_LISTEN:
                    out.append({"pid": p.pid, "cmd": cmd[:80], "addr": c.laddr.ip, "port": c.laddr.port})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def main() -> int:
    socks = listening_sockets()
    if not socks:
        print("intake のプロセスが待ち受けているソケットはない（閲覧画面などを起動してから実行する）")
        return 0
    bad = 0
    for s in socks:
        ok = s["addr"] in LOOPBACK
        bad += not ok
        print(f"{'OK' if ok else 'NG'}  {s['addr']}:{s['port']}  pid={s['pid']}  {s['cmd']}")
    print("→ すべて localhost のみ" if not bad else f"→ localhost 以外で待ち受けているものが {bad} 件")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
