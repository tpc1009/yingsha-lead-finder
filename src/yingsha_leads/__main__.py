import argparse
import fcntl
import json
from pathlib import Path
import threading
import urllib.request
import webbrowser
import uvicorn
from .app import create_app,default_data_dir


def main():
    parser=argparse.ArgumentParser(description='颖纱客户线索工具')
    parser.add_argument('--port',type=int,default=18765)
    parser.add_argument('--data-dir',type=Path,default=default_data_dir())
    parser.add_argument('--open',action='store_true')
    args=parser.parse_args()
    if not 1024<=args.port<=65535: parser.error('端口范围应为1024～65535')
    address=f'http://127.0.0.1:{args.port}'
    args.data_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (args.data_dir/'app.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                with urllib.request.urlopen(address+'/api/status',timeout=2) as r: status=json.load(r)
                if status.get('app')=='yingsha-lead-finder':
                    if args.open: webbrowser.open(address)
                    print('工具已在运行：'+address); return
            except Exception: pass
            raise SystemExit('同一数据目录已有工具进程，请关闭原进程后再启动。')
        print('颖纱客户线索工具：'+address,flush=True)
        print('运行时不调用模型。按 Ctrl+C 停止服务。',flush=True)
        if args.open:
            timer=threading.Timer(1.5,lambda:webbrowser.open(address)); timer.daemon=True; timer.start()
        uvicorn.run(create_app(args.data_dir),host='127.0.0.1',port=args.port,workers=1,access_log=False,log_level='warning')


if __name__=='__main__': main()
