"""Auditoria diaria: so avisa o que tem problema; confirma nome antes de acusar (13/10)."""
import auditoria_conteudo as A
from auditoria_conteudo import veredito

GTO = {"idx": 0, "tipo": "gto", "paciente": "MARIA DA SILVA SOUZA",
       "profissional_solicitante": "ROSEANA FORTES", "conselho_numero": "9752", "exames": ["panoramica"]}
LAU = {"idx": 2, "tipo": "laudo", "paciente": "MARIA DA SILVA SOUZA", "exames": ["panoramica"]}
IMG = {"idx": 3, "tipo": "imagem", "paciente": "MARIA DA SILVA SOUZA", "exames": ["panoramica"]}


def _ped(dent, cro):
    return {"idx": 1, "tipo": "pedido", "paciente": "Maria da Silva Souza", "dentista": dent,
            "cro": cro, "exames": ["panoramica"]}


def test_dentista_diferente_sem_cro_nao_e_alarme():
    s, m = veredito("MARIA DA SILVA SOUZA", "panoramica", [GTO, _ped("Laura de Freitas", ""), LAU, IMG])
    assert s == "INCERTO" and "sem CRO" in m[0]


def test_dentista_diferente_com_cros_diferentes_e_erro():
    s, _ = veredito("MARIA DA SILVA SOUZA", "panoramica", [GTO, _ped("Carlos Nunes", "1111"), LAU, IMG])
    assert s == "ERRADO"


def test_diaria_avisa_so_os_ruins(monkeypatch):
    monkeypatch.setattr(A, "guias_faturadas_desde", lambda h: [{"gto": "1"}, {"gto": "2"}])
    regs = [{"gto": "1", "status": "OK", "motivos": []},
            {"gto": "2", "status": "FALTA", "motivos": ["nenhum laudo anexado"]}]
    avisos = []
    A.auditoria_diaria(log=lambda m: None, _auditar=lambda **k: regs,
                       _avisar=lambda itens, total: avisos.append((itens, total)))
    assert avisos == [([regs[1]], 2)]


def test_diaria_sem_problema_nao_avisa(monkeypatch):
    monkeypatch.setattr(A, "guias_faturadas_desde", lambda h: [{"gto": "1"}])
    avisos = []
    A.auditoria_diaria(log=lambda m: None, _auditar=lambda **k: [{"gto": "1", "status": "OK", "motivos": []}],
                       _avisar=lambda itens, total: avisos.append(1))
    assert avisos == []


def test_mensagem_whatsapp():
    import notificador
    enviado = []
    notificador.avisar_auditoria([{"gto": "197330008", "paciente": "TAISE", "conta": "388336",
                                   "dia": "15/09/2026", "motivos": ["nenhum laudo anexado"]}],
                                 total=30, _post=lambda *a, **k: enviado.append(a) or True)
