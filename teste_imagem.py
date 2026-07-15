import io
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import sys

def testar_edicao(caminho_imagem):
    try:
        # Abre o arquivo de imagem em modo de leitura binária
        with open(caminho_imagem, "rb") as f:
            blob = f.read()

        print(f"-> Imagem lida com sucesso: {len(blob)} bytes")

        # Abre a imagem usando o PIL (Pillow) via manipulador na memória
        img = Image.open(io.BytesIO(blob))
        
        # Pega a largura e a altura da imagem
        largura, altura = img.size
        print(f"-> Dimensões da imagem: {largura}x{altura}")

        # Configura a edição para desenhar e carrega uma fonte legível e dinâmica
        draw = ImageDraw.Draw(img)
        tamanho_fonte = max(24, int(altura * 0.025))
        try:
            font = ImageFont.truetype("arial.ttf", tamanho_fonte)
        except Exception:
            font = ImageFont.load_default()

        # Data customizada a ser desenhada
        hoje = datetime.now()
        nova_data = hoje.strftime("%d/%m/%Y")

        print(f"-> Tentando gravar a data '{nova_data}' na imagem...")
        # Escreve a nova data no canto superior direito
        # (usamos aproximadamente 70% da largura e 5% da altura como margem)
        margem_x = int(largura * 0.7)
        margem_y = int(altura * 0.05)
        
        draw.text((margem_x, margem_y), nova_data, fill="black", font=font)

        # Exibe a imagem final simulando o que a biblioteca salvaria
        print("-> Apresentando a imagem editada. Feche a aba para finalizar o script.")
        img.show()
        print("-> Sucesso!")

    except Exception as e:
        print(f"-> ERRO ao testar a imagem: {e}")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        alvo = sys.argv[1]
    else:
        alvo = "teste.jpg"
    
    print(f"=== INICIANDO TESTE COM O ARQUIVO: {alvo} ===")
    testar_edicao(alvo)
