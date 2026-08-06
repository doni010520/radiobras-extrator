"""
test_cpf.py — Trava a busca de paciente por CPF no PRORADIS (chave nova) e o
fallback por nome. Só funções puras + orquestração com sessão fake: sem rede,
sem navegador.

Casos reais que originaram cada teste estão nomeados. Descoberta 05/08: o
endpoint search_patient_list INDEXA CPF; um CPF pode devolver MAIS de um
prontuário (duplicado) — caso ADAILDES FIUZA DOS SANTOS (29/07), CPF ->
prontuários 20040659 + 20040640.

    pytest test_cpf.py -q
"""
import extrair_anexos_dia as ax


# HTML representativo do resultado de search_patient_list (2 cards do MESMO CPF)
_HTML_2CARDS = """
<div class="results">
  <div class="patient-item" data-pat-id="20040659">
    <div class="nome">ADAILDES FIUZA DOS SANTOS</div>
    <div>Prontuário: 20040659 &nbsp; Nascimento: 01/01/1980</div>
    <a class="prontuario" href="https://radiobras.smartris.com.br/ris/ehr/record/Kh6K8O7fgmn">Prontuário</a>
    <a href="#" onclick="load_patient_profile('Kh6K8O7fgmnPIDaaa')">Perfil</a>
  </div>
  <div class="patient-item" data-pat-id="20040640">
    <div class="nome">ADAILDES FIUZA DOS SANTOS</div>
    <div>Prontuário: 20040640 &nbsp; Nascimento: 01/01/1980</div>
    <a class="prontuario" href="https://radiobras.smartris.com.br/ris/ehr/record/AbCdEfGhi">Prontuário</a>
    <a href="#" onclick="load_patient_profile('AbCdEfGhiPIDbbb')">Perfil</a>
  </div>
</div>
"""


def test_parse_cards_cpf_extrai_cod_e_pid():
    cards = ax._parse_cards_cpf(_HTML_2CARDS)
    assert [c["cod"] for c in cards] == ["20040659", "20040640"]
    assert [c["pid"] for c in cards] == ["Kh6K8O7fgmnPIDaaa", "AbCdEfGhiPIDbbb"]


def test_parse_cards_cpf_um_cpf_pode_ter_dois_prontuarios():
    # ADAILDES: o CPF junta os prontuários duplicados — é isso que aposenta o
    # casamento frágil por nome+nascimento (_gemeos_de).
    cards = ax._parse_cards_cpf(_HTML_2CARDS)
    assert len(cards) == 2


def test_parse_cards_cpf_html_vazio_nao_quebra():
    assert ax._parse_cards_cpf("") == []
    assert ax._parse_cards_cpf("<div>nada aqui</div>") == []


# HTML representativo do view_attachments
_HTML_ANEXOS = """
<div class="attachment-list">
  <div class="attachment-item" data-id="42811" data-filename="SOLICITACAO ADAILDES.pdf">
    <a href="https://radiobras.smartris.com.br/ris/patients/download_attachment/42811/20040659">baixar</a>
  </div>
  <div class="attachment-item" data-id="42812" data-filename="laudo panoramica.pdf"></div>
</div>
"""


def test_parse_anexos_view_extrai_id_filename_url():
    itens = ax._parse_anexos_view(_HTML_ANEXOS, "20040659")
    assert [i["id"] for i in itens] == ["42811", "42812"]
    assert itens[0]["filename"] == "SOLICITACAO ADAILDES.pdf"
    assert itens[0]["url"].endswith("/download_attachment/42811/20040659")


def test_parse_anexos_view_sem_link_monta_url_por_id_e_cod():
    # o 2o item nao tem <a>: a url e montada a partir de id + cod (como _abrir_anexos)
    itens = ax._parse_anexos_view(_HTML_ANEXOS, "20040659")
    assert itens[1]["url"].endswith("/download_attachment/42812/20040659")


def test_parse_anexos_view_vazio_nao_quebra():
    assert ax._parse_anexos_view("", "20040659") == []


# ── sessão HTTP fake ──────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, text="", status_code=200):
        self.text = text; self.status_code = status_code


class _FakeSess:
    def __init__(self, get_text="", post_text="", post_by_pid=None,
                 get_raises=False, get_status=200):
        self._get_text, self._post_text = get_text, post_text
        self._post_by_pid = post_by_pid or {}
        self._get_raises, self._get_status = get_raises, get_status
        self.get_calls, self.post_calls = [], []

    def get(self, url, params=None, timeout=None):
        self.get_calls.append((url, params))
        if self._get_raises:
            raise ConnectionError("rede caiu")
        return _FakeResp(self._get_text, self._get_status)

    def post(self, url, data=None, timeout=None):
        self.post_calls.append((url, data))
        pid = (data or {}).get("patient_id")
        return _FakeResp(self._post_by_pid.get(pid, self._post_text))


# ── buscar_prontuarios_por_cpf ────────────────────────────────────────────────
def test_buscar_por_cpf_usa_digitos_e_devolve_prontuarios():
    sess = _FakeSess(get_text=_HTML_2CARDS)
    pronts = ax.buscar_prontuarios_por_cpf(sess, "805.123.456-34")
    assert [p["cod"] for p in pronts] == ["20040659", "20040640"]
    url, params = sess.get_calls[0]
    assert "search_patient_list" in url
    assert params["input"] == "80512345634"        # normaliza p/ dígitos


def test_buscar_por_cpf_invalido_nao_bate_no_servidor():
    sess = _FakeSess(get_text=_HTML_2CARDS)
    assert ax.buscar_prontuarios_por_cpf(sess, "123") == []
    assert ax.buscar_prontuarios_por_cpf(sess, "") == []
    assert ax.buscar_prontuarios_por_cpf(sess, None) == []
    assert sess.get_calls == []                     # CPF malformado NÃO chama o endpoint


def test_buscar_por_cpf_erro_de_rede_cai_no_fallback():
    # M1 (review): timeout/conexão na busca por CPF não pode BLOQUEAR a guia —
    # degrada p/ [] e quem chama cai no nome, igual ao não-200.
    sess = _FakeSess(get_raises=True)
    assert ax.buscar_prontuarios_por_cpf(sess, "805.123.456-34") == []


def test_buscar_por_cpf_status_ruim_devolve_vazio():
    sess = _FakeSess(get_text=_HTML_2CARDS, get_status=500)
    assert ax.buscar_prontuarios_por_cpf(sess, "805.123.456-34") == []


# ── anexos_por_cpf (união + dedupe entre prontuários) ─────────────────────────
def test_anexos_por_cpf_uniao_e_dedupe_entre_prontuarios():
    # 2 prontuários (ADAILDES), cada um devolve os MESMOS 2 anexos: dedupe -> 2
    sess = _FakeSess(get_text=_HTML_2CARDS, post_text=_HTML_ANEXOS)
    itens, pronts = ax.anexos_por_cpf(sess, "805.123.456-34")
    assert len(pronts) == 2
    assert len(itens) == 2                          # 2+2 com dedupe por (id, filename)
    assert sess.post_calls[0][1]["patient_id"] == "Kh6K8O7fgmnPIDaaa"
    assert sess.post_calls[1][1]["patient_id"] == "AbCdEfGhiPIDbbb"


def test_anexos_por_cpf_sem_prontuario_devolve_vazio():
    sess = _FakeSess(get_text="<div>ninguém</div>")
    itens, pronts = ax.anexos_por_cpf(sess, "805.123.456-34")
    assert itens == [] and pronts == []
    assert sess.post_calls == []                    # sem prontuário, não busca anexo


def test_anexos_por_cpf_uniao_CRESCE_com_anexos_distintos():
    # o outro sentido da união: cada prontuário duplicado tem um anexo DIFERENTE
    # -> o resultado soma os dois (a solicitação pode estar no prontuário duplicado).
    anx1 = '<div class="attachment-list"><div class="attachment-item" data-id="1" data-filename="A.pdf"></div></div>'
    anx2 = '<div class="attachment-list"><div class="attachment-item" data-id="2" data-filename="B.pdf"></div></div>'
    sess = _FakeSess(get_text=_HTML_2CARDS,
                     post_by_pid={"Kh6K8O7fgmnPIDaaa": anx1, "AbCdEfGhiPIDbbb": anx2})
    itens, pronts = ax.anexos_por_cpf(sess, "805.123.456-34")
    assert sorted(i["id"] for i in itens) == ["1", "2"]


_HTML_CARD_SEM_PID = '<div data-pat-id="20099999"><div>Prontuário: 20099999</div></div>'


def test_anexos_por_cpf_card_sem_pid_e_pulado_sem_quebrar():
    # I2 (review): se um card vier sem load_patient_profile, pid=None -> pula sem
    # crashar (não dá pra listar anexo sem patient_id).
    sess = _FakeSess(get_text=_HTML_CARD_SEM_PID, post_text=_HTML_ANEXOS)
    itens, pronts = ax.anexos_por_cpf(sess, "805.123.456-34")
    assert len(pronts) == 1 and pronts[0]["pid"] is None
    assert itens == []
    assert sess.post_calls == []                    # sem pid, não chama view_attachments


# ── resolver_anexos (CPF-first / nome-fallback) ───────────────────────────────
def test_resolver_usa_cpf_quando_acha_prontuario():
    chamou_nome = {"v": False}
    def _cpf(): return (["ITEM_CPF"], [{"cod": "1", "pid": "p"}])
    def _nome():
        chamou_nome["v"] = True; return ["ITEM_NOME"]
    itens, fonte = ax.resolver_anexos("805.123.456-34", _cpf, _nome)
    assert fonte == "cpf" and itens == ["ITEM_CPF"]
    assert chamou_nome["v"] is False               # não caiu no fallback


def test_resolver_cai_no_nome_quando_cpf_nao_acha():
    # cadastro do PRORADIS sem CPF -> busca por CPF não acha -> fallback nome
    def _cpf(): return ([], [])
    def _nome(): return ["ITEM_NOME"]
    itens, fonte = ax.resolver_anexos("805.123.456-34", _cpf, _nome)
    assert fonte == "nome" and itens == ["ITEM_NOME"]


def test_resolver_sem_cpf_vai_direto_no_nome():
    def _cpf(): raise AssertionError("não deveria buscar por CPF")
    def _nome(): return ["ITEM_NOME"]
    itens, fonte = ax.resolver_anexos("", _cpf, _nome)
    assert fonte == "nome" and itens == ["ITEM_NOME"]


def test_resolver_confia_no_cpf_mesmo_sem_anexos():
    # CPF achou o prontuário certo mas ele não tem anexos: NÃO cai no nome
    # (evita puxar anexo de prontuário de homônimo). Vazio, fonte=cpf.
    def _cpf(): return ([], [{"cod": "1", "pid": "p"}])
    def _nome(): raise AssertionError("não deveria cair no nome")
    itens, fonte = ax.resolver_anexos("805.123.456-34", _cpf, _nome)
    assert fonte == "cpf" and itens == []


# ── desempate por NASCIMENTO (chave definitiva) ───────────────────────────────
# Descoberta 06/08: PRORADIS NÃO busca por carteirinha, mas o card mostra o
# Nascimento e ele BATE com o dataNascimento da guia (OdontoPrev 1981-12-02 =
# card 02/12/1981). Nascimento vira o desempatador de homônimo — caso FILIPE
# (dois "Felipe Silva dos Santos" com nascimentos diferentes).

def test_cards_por_nascimento_desempata_homonimo():
    cards = [{"cod": "111", "nascimento": "10/05/1990"},
             {"cod": "222", "nascimento": "03/08/1985"}]
    achou = ax._cards_por_nascimento(cards, "1990-05-10")   # guia em AAAA-MM-DD
    assert [c["cod"] for c in achou] == ["111"]


def test_cards_por_nascimento_aceita_iso_e_br():
    cards = [{"cod": "111", "nascimento": "02/12/1981"}]   # card PRORADIS (BR)
    assert [c["cod"] for c in ax._cards_por_nascimento(cards, "1981-12-02")] == ["111"]
    assert [c["cod"] for c in ax._cards_por_nascimento(cards, "02/12/1981")] == ["111"]


def test_cards_por_nascimento_sem_match_vazio():
    cards = [{"cod": "111", "nascimento": "10/05/1990"}]
    assert ax._cards_por_nascimento(cards, "01/01/2000") == []


def test_cards_por_nascimento_guia_sem_data_nao_filtra():
    # sem nascimento na guia não há desempate seguro -> devolve todos (o chamador
    # cai na lógica de antes). Nunca "inventa" um desempate.
    cards = [{"cod": "111", "nascimento": "10/05/1990"},
             {"cod": "222", "nascimento": "03/08/1985"}]
    assert len(ax._cards_por_nascimento(cards, "")) == 2
    assert len(ax._cards_por_nascimento(cards, None)) == 2


def test_cards_por_nascimento_card_sem_nascimento_nao_casa():
    # card "Novo Paciente" (sem nascimento) nunca casa por data
    cards = [{"cod": "111", "nascimento": ""}]
    assert ax._cards_por_nascimento(cards, "10/05/1990") == []
