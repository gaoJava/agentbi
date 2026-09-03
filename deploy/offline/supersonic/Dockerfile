ARG JAVA_IMAGE=eclipse-temurin:8-jre-jammy
FROM ${JAVA_IMAGE}

WORKDIR /opt/supersonic
COPY launchers/standalone/target/launchers-standalone-0.8.6-SNAPSHOT-bin.tar.gz /tmp/supersonic.tar.gz
RUN tar -xzf /tmp/supersonic.tar.gz --strip-components=1 -C /opt/supersonic \
    && rm /tmp/supersonic.tar.gz \
    && mkdir -p /opt/supersonic/data

EXPOSE 9080
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=10 \
  CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/9080' || exit 1

# SuperSonic patches a few HanLP classes in common-*.jar. Keep that jar ahead of
# hanlp-portable on every JVM/architecture; wildcard-only classpaths have no
# portable ordering guarantee and can otherwise fail with NoSuchFieldError.
CMD ["java", "-Dserver.address=0.0.0.0", "-cp", "conf:lib/common-0.8.6-SNAPSHOT.jar:lib/*", "com.tencent.supersonic.StandaloneLauncher"]
