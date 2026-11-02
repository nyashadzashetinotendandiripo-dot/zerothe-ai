import subprocess, json, os

msg = json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'test','version':'1.0'}}}})
msg_bytes = msg.encode()
header = f'Content-Length: {len(msg_bytes)}\r\n\r\n'.encode()
env = dict(os.environ)
env['QUICKBOOKS_CLIENT_ID']='ABopepXgWmF34HUD3nCC5EF2fGVd8OllRFMvmRrQSidri5sYSK'
env['QUICKBOOKS_CLIENT_SECRET']='A4KqW2pVwSSjQvYB216a4iRoAlwNIIEfkDaplz92'
env['QUICKBOOKS_ENVIRONMENT']='production'
proc = subprocess.Popen(['/usr/local/bin/node','/data/home/zerothebot/mcp-deploy/quickbooks-online-mcp-server/dist/index.js'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
out, err = proc.communicate(input=header + msg_bytes, timeout=10)
print('STDOUT:', out.decode())
print('STDERR:', err.decode()[:500])
print('RC:', proc.returncode)
