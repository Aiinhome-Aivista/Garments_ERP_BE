import urllib.request, json, sys
req = urllib.request.Request('http://127.0.0.1:5000/auth/login', data=json.dumps({'username':'admin','password':'password'}).encode('utf-8'), headers={'Content-Type': 'application/json'})
try:
  urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
  print(e.code, e.read().decode())
