"""Wrapper for v7 — full sync with permissions"""
import subprocess, sys
sys.exit(subprocess.run([sys.executable, "sync_one_user.py",
    "ory_at_uhxyMGq8C5rNIGER8I5VulU8HBm1dnPbC6Yq8MMjArE.isEJtOLP-MmTbz1v7dIY1pUo8GCgp5jzjfkGqS4gMbM",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0MjUyNjc0LCJpc3MiOiJiaXNoZW5nIn0.NF_QfO-80aRH3DjYW8Aaql-F5FegeMEGg2GAoIYTeO4",
    "gns://1A71734693F8464A9B8C1980D4AFBB44",
    "公司资质_v7"], cwd=r"D:\aishu\code\anyshare-sync").returncode)
