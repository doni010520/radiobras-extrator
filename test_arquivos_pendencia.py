"""PASTA DA PENDENCIA: os arquivos que o robo ja baixou, guardados para a operacao
conferir e anexar a mao quando o robo nao consegue.

Pedido da Andrea (print de 17/08, item 6): "criar pasta com imagens resolvidas para
casos de nao conseguir ler solicitacoes, depois ela anexa tudo".

Estrutura definida pelo dono (22/08): **plano / data / GTO_Paciente**

    /dados/pendencias/388336/2026-08-14/196215069_JACIARA_RIBEIRO_SANTANA/

O robo JA baixa esses arquivos durante a rodada, numa pasta temporaria que e
apagada no fim. "Criar a pasta" e parar de apagar — nao ha download novo.

Volume /dados montado no EasyPanel em 22/08. Sem volume o disco do container e
apagado a cada deploy (foi o que perdeu o SQLite antes), por isso o codigo tem que
DEGRADAR sem quebrar faturamento: se a pasta nao existe ou nao da para escrever,
segue a vida — guardar arquivo nunca pode derrubar uma rodada."""
import io
import os
import time

import arquivos_pendencia as ap


# ── o caminho ───────────────────────────────────────────────────────────────
def test_estrutura_plano_data_gto_paciente():
    c = ap.caminho_da_guia("/dados/pendencias", "388336", "14/08/2026",
                           "196215069", "JACIARA RIBEIRO SANTANA")
    assert c.replace("\\", "/") == ("/dados/pendencias/388336/2026-08-14/"
                                    "196215069_JACIARA_RIBEIRO_SANTANA")


def test_data_vira_ano_mes_dia_para_ordenar():
    """DD/MM/AAAA ordena errado no explorador de arquivos; AAAA-MM-DD ordena so."""
    c = ap.caminho_da_guia("/b", "388336", "03/06/2026", "1", "X")
    assert "/2026-06-03/" in c.replace("\\", "/")


def test_acento_e_cedilha_viram_ascii():
    # nome de pasta com acento quebra em zip/download e em alguns navegadores
    c = ap.caminho_da_guia("/b", "388336", "14/08/2026", "9", "JOSÉ ANTÔNIO GONÇALVES")
    assert "JOSE_ANTONIO_GONCALVES" in c


def test_barra_no_nome_nao_cria_subpasta():
    """O pior caso: paciente com '/' no nome criaria uma pasta a mais e os arquivos
    sumiriam de onde a tela procura."""
    c = ap.caminho_da_guia("/b", "388336", "14/08/2026", "9", "MARIA / ANA")
    # o que importa: a barra do NOME nao virou separador de pasta
    assert os.path.basename(c) == "9_MARIA_ANA"
    assert c.replace("\\", "/") == "/b/388336/2026-08-14/9_MARIA_ANA"


def test_nome_muito_longo_e_cortado():
    c = ap.caminho_da_guia("/b", "388336", "14/08/2026", "9", "A" * 300)
    pasta = os.path.basename(c)
    assert len(pasta) <= 120


def test_paciente_vazio_ainda_gera_pasta_pelo_gto():
    c = ap.caminho_da_guia("/b", "388336", "14/08/2026", "196215069", "")
    assert os.path.basename(c) == "196215069"


def test_sem_base_devolve_vazio():
    # PENDENCIAS_DIR nao configurada -> recurso desligado, nao explode
    assert ap.caminho_da_guia("", "388336", "14/08/2026", "9", "X") == ""
    assert ap.caminho_da_guia(None, "388336", "14/08/2026", "9", "X") == ""


# ── guardar ─────────────────────────────────────────────────────────────────
def test_guarda_os_arquivos(tmp_path):
    origem = tmp_path / "dl"
    origem.mkdir()
    (origem / "LAUDO_PANORAMICA_1_OFICIAL.pdf").write_bytes(b"%PDF-1.4 laudo")
    (origem / "ENTREGA_abc.jpg").write_bytes(b"\xff\xd8imagem")
    base = str(tmp_path / "dados")
    r = ap.guardar(base, "388336", "14/08/2026", "196215069", "JACIARA",
                   str(origem), ["LAUDO_PANORAMICA_1_OFICIAL.pdf", "ENTREGA_abc.jpg"])
    assert r["qtd"] == 2
    assert sorted(os.listdir(r["pasta"])) == ["ENTREGA_abc.jpg",
                                              "LAUDO_PANORAMICA_1_OFICIAL.pdf"]


def test_guardar_duas_vezes_nao_duplica(tmp_path):
    """Rodar o mesmo dia de novo nao pode encher a pasta de copia — a operadora
    abriria e veria o mesmo laudo tres vezes."""
    origem = tmp_path / "dl"; origem.mkdir()
    (origem / "a.pdf").write_bytes(b"x")
    base = str(tmp_path / "dados")
    for _ in range(3):
        r = ap.guardar(base, "388336", "14/08/2026", "9", "X", str(origem), ["a.pdf"])
    assert len(os.listdir(r["pasta"])) == 1


def test_guardar_sem_base_nao_faz_nada_e_nao_levanta(tmp_path):
    """Volume nao montado: guardar arquivo NUNCA pode derrubar uma rodada de
    faturamento. Falha quieto."""
    origem = tmp_path / "dl"; origem.mkdir()
    (origem / "a.pdf").write_bytes(b"x")
    r = ap.guardar("", "388336", "14/08/2026", "9", "X", str(origem), ["a.pdf"])
    assert r["qtd"] == 0


def test_arquivo_que_sumiu_no_meio_nao_derruba(tmp_path):
    origem = tmp_path / "dl"; origem.mkdir()
    base = str(tmp_path / "dados")
    r = ap.guardar(base, "388336", "14/08/2026", "9", "X", str(origem),
                   ["nao_existe.pdf"])
    assert r["qtd"] == 0
    assert r.get("erros")


# ── listar (para a tela) ────────────────────────────────────────────────────
def test_lista_com_tamanho_e_tipo(tmp_path):
    origem = tmp_path / "dl"; origem.mkdir()
    (origem / "LAUDO_X_1_OFICIAL.pdf").write_bytes(b"%PDF" + b"z" * 100)
    (origem / "ENTREGA_a.jpg").write_bytes(b"\xff\xd8" + b"z" * 50)
    base = str(tmp_path / "dados")
    ap.guardar(base, "388336", "14/08/2026", "9", "X", str(origem),
               ["LAUDO_X_1_OFICIAL.pdf", "ENTREGA_a.jpg"])
    itens = ap.listar(base, "388336", "14/08/2026", "9", "X")
    assert len(itens) == 2
    por_nome = {i["nome"]: i for i in itens}
    assert por_nome["ENTREGA_a.jpg"]["tipo"] == "imagem"
    assert por_nome["LAUDO_X_1_OFICIAL.pdf"]["tipo"] == "pdf"
    assert por_nome["ENTREGA_a.jpg"]["bytes"] == 52


def test_listar_pasta_inexistente_devolve_vazio(tmp_path):
    assert ap.listar(str(tmp_path), "388336", "14/08/2026", "9", "X") == []


# ── seguranca do caminho ────────────────────────────────────────────────────
def test_nao_deixa_escapar_da_pasta(tmp_path):
    """A rota do frontend recebe o nome do arquivo por URL. Sem esta trava daria
    para pedir '../../.env' e baixar as credenciais do sistema."""
    base = str(tmp_path / "dados")
    for mau in ("../../.env", "..\\..\\.env", "/etc/passwd", "sub/dir/a.pdf"):
        assert ap.caminho_do_arquivo(base, "388336", "14/08/2026", "9", "X", mau) is None


def test_arquivo_legitimo_resolve(tmp_path):
    origem = tmp_path / "dl"; origem.mkdir()
    (origem / "a.pdf").write_bytes(b"x")
    base = str(tmp_path / "dados")
    ap.guardar(base, "388336", "14/08/2026", "9", "X", str(origem), ["a.pdf"])
    p = ap.caminho_do_arquivo(base, "388336", "14/08/2026", "9", "X", "a.pdf")
    assert p and os.path.isfile(p)


# ── expurgo ─────────────────────────────────────────────────────────────────
def test_expurga_o_que_passou_do_prazo(tmp_path):
    """Sem expurgo isso vira gigabytes de imagem de paciente parada no disco, sem
    ninguem lembrar por que — problema de LGPD, nao de espaco."""
    base = str(tmp_path / "dados")
    origem = tmp_path / "dl"; origem.mkdir()
    (origem / "a.pdf").write_bytes(b"x")
    velha = ap.guardar(base, "388336", "01/06/2026", "1", "VELHA", str(origem), ["a.pdf"])
    nova = ap.guardar(base, "388336", "22/08/2026", "2", "NOVA", str(origem), ["a.pdf"])
    antigo = time.time() - 60 * 60 * 24 * 90
    os.utime(velha["pasta"], (antigo, antigo))
    r = ap.expurgar(base, dias=30)
    assert r["removidas"] >= 1
    assert not os.path.isdir(velha["pasta"])
    assert os.path.isdir(nova["pasta"])


def test_expurgo_sem_base_nao_levanta():
    assert ap.expurgar("", dias=30)["removidas"] == 0
