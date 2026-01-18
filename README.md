# MaxProxy

## Настройка 
```
pip3 install -r ./requirements.txt 
```

Скопировать .env.example в .env, настроить следующие параметры
```
# Client Configuration
# Phone number for the client
CLIENT_PHONE=+1234567890

# Server Configuration
# Phone number for the server (required)
SERVER_PHONE=+0987654321

# SOCKS5 Proxy Configuration
# Host address to bind the proxy server
PROXY_HOST=0.0.0.0

# Port number for the proxy server
PROXY_PORT=1080

# Username for SOCKS5 authentication
PROXY_USERNAME=username

# Password for SOCKS5 authentication
PROXY_PASSWORD=password
```

## Запуск
На клиенте
```
python3 main_client.py
```
На сервере
```
python3 main_server.py
```
