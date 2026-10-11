"""交互式保存凭证，输入关闭回显，不写配置文件。"""
import argparse
import getpass
from secrets_store import default_secret_store
parser = argparse.ArgumentParser()
parser.add_argument('name', choices=['nextemby', 'bridge'])
args = parser.parse_args()
value = getpass.getpass('请输入凭证（输入隐藏）：').strip()
if not value or '\n' in value or '\r' in value:
    raise SystemExit('凭证格式不正确')
default_secret_store().set(args.name, value)
print('已保存到当前部署的受保护凭据存储。')
