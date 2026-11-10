"""Open the screening demo in its own local data directory."""
import argparse
from pathlib import Path

from app import instance_lock, serve
from engine import Store


def main():
    parser = argparse.ArgumentParser(description='开源版筛选演示，进入策略筛选后点击「先看虚构示例」')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).parent/'local-demo-data')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    with instance_lock(args.data_dir):
        serve(Store(args.data_dir), args.port, not args.no_browser)


if __name__ == '__main__':
    main()
