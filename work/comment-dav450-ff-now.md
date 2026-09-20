只做线性 FF，禁止全量 pytest / 禁止改代码 / 禁止重启：

```bash
git fetch origin
# trunk 必须仍是 578c01d195ab388615e14372992b2bd64ba18bec
git push origin 52353ed8d28d0dcee50be370b01ec42059df9836:refs/heads/codex/dav-4-p2a-trunk
# 读回必须等于 52353ed8d28d0dcee50be370b01ec42059df9836
```

完成后关卡。部署另卡 DAV-449。
