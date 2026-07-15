import os
import io
import re
import sys
import json
import time
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# Tenta carregar dotenv caso o ambiente puxe do arquivo
if os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("GEMINI_API_KEY"):
                val = line.split("=", 1)[1].strip().strip("'").strip('"')
                os.environ["GEMINI_API_KEY"] = val

gemini_key = os.environ.get("GEMINI_API_KEY")
if not gemini_key:
    print("ERRO: Chave GEMINI_API_KEY não localizada. Defina a variável para o teste.")
    sys.exit(1)

from google import genai
from google.genai import types

def _parse_br_date(s):
    try:
        import datetime as _dt
        p = re.findall(r"\d+", str(s))
        if len(p) < 3: return None
        d, m, y = int(p[0]), int(p[1]), int(p[2])
        if y < 100: y += 2000
        return _dt.date(y, m, d)
    except Exception:
        return None

_DECISAO_PROMPT = """Acima estão VÁRIOS anexos do prontuário, indexados ([anexo 0], [anexo 1], ...).
CONTEXTO DA GTO -> paciente: {paciente} | exames esperados: {exames} | DATA DO EXAME: {data_exame}

Você é auditor de solicitações odontológicas. Identifique QUAL anexo é a SOLICITACAO/REQUISIÇÃO de exames que corresponde a ESTA GTO: mesmo paciente e exames compatíveis. Ignore laudos, raios-x e faturas.

SOBRE A DATA: leia a data escrita na solicitação. Se a data for antiga, ou se não houver data, não tem problema, apenas retorne a data encontrada (ou null) e a localização dela na imagem.

SOBRE A ASSINATURA: identifique a área de assinatura do médico/dentista na parte inferior da solicitação e retorne suas coordenadas em box_assinatura. Normalmente fica no centro ou canto inferior da folha.

SOBRE OS EXAMES: leia a solicitação INTEIRA e liste TODOS os exames pedidos. A solicitação SERVE se CONTÉM os exames da GTO ({exames}).

Responda APENAS JSON (sem markdown):
{{"indice_solicitacao": <int>, "tipo": "digitada"|"manuscrita"|null,
"legivel": <bool>, "paciente_lido": "<str>", "exames_lidos": [<str>],
"exames_batem": <bool>, "data_solicitacao": "<DD/MM/AAAA ou null>",
"data_bate": <bool>, "box_data": [<ymin>, <xmin>, <ymax>, <xmax>] ou null (valores 0-1000),
"box_assinatura": [<ymin>, <xmin>, <ymax>, <xmax>] ou null (valores 0-1000),
"confianca": "alta"|"media"|"baixa", "anexar": <bool>, "motivo": "<curto>"}}

Regra: Se exames_batem=true e a imagem for legível, marque anexar=true (mesmo que a data esteja ausente ou antiga).
"""

def testar_gemini(caminho):
    print(f"=== INICIANDO INTEGRAÇÃO GEMINI PARA: {os.path.basename(caminho)} ===")
    
    with open(caminho, "rb") as f:
        blob = f.read()

    # Define mimetypes para a IA
    mime = "image/jpeg" if caminho.lower().endswith(".jpg") or caminho.lower().endswith(".jpeg") else "image/png"
    
    contents = []
    contents.append(f"[anexo 0] {os.path.basename(caminho)}")
    contents.append(types.Part.from_bytes(data=blob, mime_type=mime))
    
    # Valores Mockados pra forçar o AI a confirmar que o exame 'bate' o máximo possível
    contents.append(_DECISAO_PROMPT.format(
        paciente="PACIENTE DE TESTE", 
        exames="['radiografia panorâmica', 'periapical']", 
        data_exame="01/06/2026"
    ))

    client = genai.Client(api_key=gemini_key)
    print("-> Enviando imagem para o Gemini 2.5 Flash analisar aguardando JSON...")
    
    # Processa Resposta
    r = client.models.generate_content(model="gemini-2.5-flash", contents=contents)
    txt = re.sub(r"^```json|^```|```$", "", (r.text or "").strip(), flags=re.M).strip()
    
    try:
        dec = json.loads(txt)
    except json.JSONDecodeError:
        print(f"-> FALHA: O Gemini não retornou um JSON válido. Retornou: \n{txt}")
        return

    print("-> RESPOSTA DO GEMINI AVALIADA:")
    print(json.dumps(dec, indent=2, ensure_ascii=False))

    # A partir daqui recriamos exatamente o mesmo bloco do "esteira.py"
    idx = dec.get("indice_solicitacao")
    
    # Forçar exames batem para garantir o teste da manipulação de imagem mesmo se a IA achar que o mock não é igual
    if dec.get("indice_solicitacao") is not None:
        dec["exames_batem"] = True 
        idx = 0
    
    if isinstance(idx, int) and idx == 0 and dec.get("exames_batem"):
        print("\n-> ENGATILHANDO BLOCO DE EDICAO DE IMAGEM DA ESTEIRA...")
        data_lida_str = dec.get("data_solicitacao")
        data_lida = _parse_br_date(data_lida_str) if data_lida_str else None
        hoje = datetime.now()
        
        precisa_manipular = False
        tipo = None
        
        if not data_lida:
            precisa_manipular = True; tipo = 'inserir'
            print("-> CENARIO: Sem data localizada (ou ilegivel). O sistema escolheu [inserir]")
        elif (hoje - data_lida).days > 60:
            precisa_manipular = True; tipo = 'atualizar'
            print("-> CENARIO: Data encontrada anterior a 60 dias. O sistema escolheu [atualizar]")
        else:
            print(f"-> A data '{data_lida_str}' está legivel e dentro de 60 dias. O script não julga necessário alterar.")

        if precisa_manipular and "image" in mime.lower():
            try:
                img = Image.open(io.BytesIO(blob))
                draw = ImageDraw.Draw(img)
                largura, altura = img.size
                nova_data = hoje.strftime("%d/%m/%Y")
                
                tamanho_fonte = max(24, int(altura * 0.025))
                try:
                    font = ImageFont.truetype("arial.ttf", tamanho_fonte)
                except Exception:
                    try:
                        font = ImageFont.truetype("LiberationSans-Regular.ttf", tamanho_fonte)
                    except Exception:
                        font = ImageFont.load_default()

                if tipo == 'atualizar' and dec.get("box_data"):
                    print("-> APAGANDO data antiga e pintando nova!")
                    ymin, xmin, ymax, xmax = dec["box_data"]
                    # Apaga data antiga com retângulo branco
                    draw.rectangle([int((xmin/1000)*largura), int((ymin/1000)*altura), 
                                    int((xmax/1000)*largura), int((ymax/1000)*altura)], fill="white")
                    draw.text((int((xmin/1000)*largura), int((ymin/1000)*altura)), nova_data, fill="black", font=font)
                elif tipo == 'inserir':
                    box_ass = dec.get("box_assinatura")
                    if box_ass:
                        ymin_a, xmin_a, ymax_a, xmax_a = box_ass
                        pos_x = int(((xmin_a + xmax_a) / 2 / 1000) * largura)
                        pos_y = int((ymax_a / 1000) * altura) + 4
                        print(f"-> INSERINDO data na area de assinatura: x={pos_x}, y={pos_y}")
                    else:
                        pos_x = int(largura * 0.50)
                        pos_y = int(altura * 0.85)
                        print("-â-> box_assinatura nao retornou. Usando fallback centro-inferior.")
                    draw.text((pos_x, pos_y), nova_data, fill="black", font=font)

                # Mostra o resultado final visual
                print("-> IMAGEM PRONTA! Apresentando na tela...")
                img.show()
                print("-> FIM DA SIMULACAO E DA EDICAO DA ESTEIRA!")
            except Exception as e:
                print(f"-> FALHA AO EDITAR a imagem com Pillow: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("FORNEÇA o caminho de uma solicitacao para o teste: python teste_gemini.py 'C:\caminho\imagem.jpg'")
        sys.exit(1)
    
    testar_gemini(sys.argv[1])
