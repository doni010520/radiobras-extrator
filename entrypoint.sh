#!/bin/sh
# Inicializacao do container.
#
# SAIDA DO ODONTOPREV PELO COMPUTADOR DA CLINICA (16/09/2026). A FlameProxies
# zerou e o OdontoPrev bloqueia IP de datacenter. Com TS_AUTHKEY definida, o
# container entra na rede Tailscale em modo userspace (sem /dev/net/tun, sem
# privilegio) e usa TS_EXIT_NODE como saida. A aplicacao aponta
# ODONTO_PROXY_URL=http://127.0.0.1:1056 (proxy HTTP local do tailscaled), entao
# SO o trafego do OdontoPrev sai pela clinica; PRORADIS e o resto seguem direto.
#
# Falha do Tailscale NUNCA derruba o site: loga e sobe o gunicorn assim mesmo.
# Sem TS_AUTHKEY, o comportamento e identico ao de antes.

TS_DIR=/tmp/tailscale
TS_SOCK="$TS_DIR/tailscaled.sock"

if [ -n "$TS_AUTHKEY" ]; then
    mkdir -p "$TS_DIR"
    echo "[tailscale] iniciando tailscaled (userspace)"
    tailscaled --tun=userspace-networking --state=mem: --socket="$TS_SOCK" \
        --socks5-server=127.0.0.1:1055 \
        --outbound-http-proxy-listen=127.0.0.1:1056 \
        >"$TS_DIR/tailscaled.log" 2>&1 &

    i=0
    while [ ! -S "$TS_SOCK" ] && [ "$i" -lt 40 ]; do
        sleep 0.5
        i=$((i + 1))
    done

    if tailscale --socket="$TS_SOCK" up \
            --authkey="$TS_AUTHKEY" \
            --hostname="${TS_HOSTNAME:-radiobras-vps}" \
            --timeout=60s; then
        echo "[tailscale] conectado"
        if [ -n "$TS_EXIT_NODE" ]; then
            if tailscale --socket="$TS_SOCK" set --exit-node="$TS_EXIT_NODE"; then
                echo "[tailscale] exit node: $TS_EXIT_NODE"
            else
                echo "[tailscale] FALHOU ao usar o exit node $TS_EXIT_NODE (aprovado no site?)"
            fi
        else
            echo "[tailscale] TS_EXIT_NODE vazio - sem saida pela clinica"
        fi
        tailscale --socket="$TS_SOCK" status || true
    else
        echo "[tailscale] 'up' FALHOU (chave vencida/revogada?) - app sobe sem Tailscale"
        tail -n 20 "$TS_DIR/tailscaled.log" 2>/dev/null || true
    fi
fi

# 1 worker (job store e em memoria) + threads para o job em background.
# timeout 0 porque a extracao de um dia pode levar minutos.
exec gunicorn --workers 1 --threads 8 --timeout 0 --bind 0.0.0.0:5000 app:app
