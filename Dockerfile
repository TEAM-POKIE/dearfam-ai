# Dockerfile
FROM python:3.11-slim

# 작업 디렉토리
WORKDIR /my-app-python

# 의존성 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드 복사
COPY . .

# 포트 열기 (필요 시 수정)
EXPOSE 8000

# 앱 실행 명령어 (run.py가 진입점이라면 아래처럼)
CMD ["python", "run.py"]