"""Pasta da pendência: os arquivos que o robô já baixou, guardados para a operação
conferir e anexar à mão quando o robô não consegue.

Pedido da Andrea (17/08, item 6): *"criar pasta com imagens resolvidas para casos de
não conseguir ler solicitações, depois ela anexa tudo"*.

Estrutura definida pelo dono (22/08) — **plano / data / GTO_Paciente**:

    /dados/pendencias/388336/2026-08-14/196215069_JACIARA_RIBEIRO_SANTANA/
        SOLICITACAO_0__JACIARA.pdf
        LAUDO_PERIAPICAL_40342657_OFICIAL.pdf
        ENTREGA_41961ce344.jpg

O robô **já baixa** esses arquivos durante a rodada, numa pasta temporária que é
apagada no fim (`shutil.rmtree` do `_att_dir`). Guardar é parar de apagar — não há
download novo, não há chamada de rede a mais.

**Falha quieto, sempre.** Volume não montado, disco cheio, permissão negada: tudo
retorna vazio e a rodada segue. Guardar arquivo para conferência nunca pode derrubar
um faturamento — o valor está no faturamento, não na pasta.

**Nada aqui é servido como estático.** São laudo e imagem de paciente (LGPD): a
tela lê por rota autenticada, e `caminho_do_arquivo` recusa qualquer nome que tente
escapar da pasta da guia.
"""
import os
import re
import shutil
import time
import unicodedata

# Acima disso o nome da pasta começa a estourar limite de caminho no Windows e em
# alguns clientes de zip. O GTO na frente garante que o corte nunca colide.
MAX_NOME_PASTA = 120
RETENCAO_PADRAO_DIAS = 30

_IMG = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_PDF = (".pdf",)


def base_dir() -> str:
    """Raiz configurada (`PENDENCIAS_DIR`). Vazio = recurso desligado."""
    return (os.environ.get("PENDENCIAS_DIR") or "").strip()


def _ascii(t) -> str:
    """Sem acento — nome de pasta com acento quebra zip e alguns navegadores."""
    t = unicodedata.normalize("NFKD", str(t or ""))
    return "".join(c for c in t if not unicodedata.combining(c))


def _sanitiza(nome) -> str:
    """Só letras, números, ponto, hífen e underscore.

    A barra é o caso perigoso: paciente com '/' no nome criaria uma subpasta a mais
    e os arquivos sumiriam de onde a tela procura."""
    t = _ascii(nome).upper()
    t = re.sub(r"[^A-Z0-9._-]+", "_", t).strip("_.")
    return re.sub(r"_+", "_", t)


def _data_iso(dia) -> str:
    """DD/MM/AAAA -> AAAA-MM-DD. Ordena sozinho no explorador de arquivos."""
    d = str(dia or "").strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", d)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else _sanitiza(d) or "sem-data"


def caminho_da_guia(base, plano, dia, gto, paciente) -> str:
    """Pasta desta guia. String vazia quando o recurso está desligado."""
    if not base:
        return ""
    pac = _sanitiza(paciente)
    g = _sanitiza(gto) or "sem-gto"
    pasta = (g + "_" + pac) if pac else g
    return os.path.join(str(base), _sanitiza(plano) or "sem-plano",
                        _data_iso(dia), pasta[:MAX_NOME_PASTA])


def guardar(base, plano, dia, gto, paciente, pasta_origem, arquivos) -> dict:
    """Copia os arquivos do plano para a pasta da guia. Nunca levanta.

    Idempotente: rodar o mesmo dia de novo não duplica — a operadora abriria a
    pasta e veria o mesmo laudo três vezes."""
    vazio = {"qtd": 0, "pasta": "", "arquivos": [], "erros": []}
    destino = caminho_da_guia(base, plano, dia, gto, paciente)
    if not destino or not pasta_origem:
        return vazio
    erros, postos = [], []
    try:
        os.makedirs(destino, exist_ok=True)
    except Exception as e:
        return dict(vazio, erros=[f"criar pasta: {str(e)[:100]}"])
    for nome in (arquivos or []):
        try:
            org = os.path.join(str(pasta_origem), os.path.basename(str(nome)))
            if not os.path.isfile(org):
                erros.append(f"{nome}: nao encontrado na pasta da rodada")
                continue
            dst = os.path.join(destino, os.path.basename(str(nome)))
            if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(org):
                postos.append(os.path.basename(str(nome)))
                continue        # ja esta la, mesmo tamanho -> nao recopia
            shutil.copy2(org, dst)
            postos.append(os.path.basename(str(nome)))
        except Exception as e:
            erros.append(f"{nome}: {str(e)[:80]}")
    return {"qtd": len(postos), "pasta": destino, "arquivos": postos, "erros": erros}


def _tipo(nome) -> str:
    n = str(nome or "").lower()
    if n.endswith(_IMG):
        return "imagem"
    if n.endswith(_PDF):
        return "pdf"
    return "outro"


def listar(base, plano, dia, gto, paciente) -> list:
    """O que há na pasta da guia — nome, bytes e tipo, para a tela montar
    miniatura, visualização e download."""
    destino = caminho_da_guia(base, plano, dia, gto, paciente)
    if not destino or not os.path.isdir(destino):
        return []
    out = []
    try:
        for nome in sorted(os.listdir(destino)):
            p = os.path.join(destino, nome)
            if not os.path.isfile(p):
                continue
            out.append({"nome": nome, "bytes": os.path.getsize(p), "tipo": _tipo(nome)})
    except Exception:
        return []
    return out


def caminho_do_arquivo(base, plano, dia, gto, paciente, nome):
    """Caminho absoluto de UM arquivo, ou None se o nome for suspeito.

    A rota do frontend recebe este nome pela URL. Sem esta trava daria para pedir
    `../../.env` e baixar as credenciais do sistema."""
    destino = caminho_da_guia(base, plano, dia, gto, paciente)
    if not destino or not nome:
        return None
    n = str(nome)
    if n != os.path.basename(n) or n in (".", "..") or "/" in n or "\\" in n:
        return None
    alvo = os.path.abspath(os.path.join(destino, n))
    if os.path.commonpath([alvo, os.path.abspath(destino)]) != os.path.abspath(destino):
        return None
    return alvo if os.path.isfile(alvo) else None


def expurgar(base, dias: int = RETENCAO_PADRAO_DIAS) -> dict:
    """Apaga pasta de guia mais velha que `dias`.

    Sem isto vira gigabytes de imagem de paciente parada no disco, sem ninguém
    lembrar por quê — problema de LGPD, não de espaço. O dado é transitório: depois
    que a operadora anexa, a pasta é peso morto."""
    if not base or not os.path.isdir(str(base)):
        return {"removidas": 0, "erros": []}
    limite = time.time() - (int(dias) * 86400)
    removidas, erros = 0, []
    try:
        for plano in os.listdir(base):
            p_plano = os.path.join(base, plano)
            if not os.path.isdir(p_plano):
                continue
            for data in os.listdir(p_plano):
                p_data = os.path.join(p_plano, data)
                if not os.path.isdir(p_data):
                    continue
                for guia in os.listdir(p_data):
                    p_guia = os.path.join(p_data, guia)
                    if not os.path.isdir(p_guia):
                        continue
                    try:
                        if os.path.getmtime(p_guia) < limite:
                            shutil.rmtree(p_guia, ignore_errors=True)
                            removidas += 1
                    except Exception as e:
                        erros.append(f"{guia}: {str(e)[:60]}")
                # varre de baixo pra cima: pasta de dia/plano vazia tambem sai
                for vazia in (p_data, p_plano):
                    try:
                        if os.path.isdir(vazia) and not os.listdir(vazia):
                            os.rmdir(vazia)
                    except Exception:
                        pass
    except Exception as e:
        erros.append(str(e)[:100])
    return {"removidas": removidas, "erros": erros}
