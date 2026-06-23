FROM eclipse-mosquitto:2.0

COPY config/mosquitto.conf /mosquitto/config/mosquitto.conf

RUN mosquitto_passwd -b -c /mosquitto/config/passwd iot_user iot_pass_2026
