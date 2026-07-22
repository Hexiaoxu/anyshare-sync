#!/bin/bash
# 1. 获取应用Token
echo "=== App Token ==="
APP_TOKEN=$(curl -s -X POST "https://5j-zsgl.powerchina.cn/oauth2/token" \
  -u "9d0c5250-25d9-43d3-85fa-c2a3f8f419f4:Il6I-gwxj~03" \
  -d "grant_type=client_credentials&scope=all" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "APP_TOKEN: ${APP_TOKEN:0:30}..."

# 2. 换取用户Token
echo ""
echo "=== User Token (5jliming1) ==="
curl -s -X POST "https://5j-zsgl.powerchina.cn/api/authentication/v1/access_token" \
  -u "9d0c5250-25d9-43d3-85fa-c2a3f8f419f4:Il6I-gwxj~03" \
  -H "Content-Type: application/json" \
  -d '{"account":"5jliming1"}'

echo ""

# 3. 用应用Token访问部门库列表
echo ""
echo "=== 部门库列表 ==="
curl -s "https://5j-zsgl.powerchina.cn/api/efast/v1/doc-lib/department?offset=0&limit=5" \
  -H "Authorization: Bearer $APP_TOKEN"

echo ""
echo "=== 知识库列表 ==="
curl -s "https://5j-zsgl.powerchina.cn/api/efast/v1/doc-lib/knowledge?offset=0&limit=5" \
  -H "Authorization: Bearer $APP_TOKEN"
