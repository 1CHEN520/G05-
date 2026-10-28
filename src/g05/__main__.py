"""允许 ``python -m g05 <子命令>``。"""

from g05.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
