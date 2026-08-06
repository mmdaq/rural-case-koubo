# 农村集体资产案例口播文案机器人 · 镜像
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

# 数据卷（去重库与输出）
VOLUME ["/app/data"]

# 常驻调度（默认每天 08:00）
CMD ["python", "main.py", "daemon"]
