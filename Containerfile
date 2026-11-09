FROM registry.access.redhat.com/ubi9/python-314:latest

WORKDIR /opt/app-root/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8080
CMD ["fastapi", "run", "main.py", "--port", "8080"]
