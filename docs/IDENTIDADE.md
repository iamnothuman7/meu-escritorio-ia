# Identidade visual — NABIO Office AI

A referência fornecida é o arquivo `Manual de Marca NABIO Office AI.png`. O sistema usa a identidade escura, a fita em forma de N, a órbita e a estrela apresentados nesse material.

## Paleta de referência

| Uso | Cor |
| --- | --- |
| Base escura | `#0B1020` |
| Principal, estratégia e ação | `#7C3AED` |
| Secundária, tecnologia e conexões | `#06B6D4` |
| Sucesso e entrega | `#22C55E` |
| Atenção e prioridade | `#F59E0B` |
| Texto de contraste | `#F8FAFC` |

As superfícies da aplicação usam `#11182B` e `#151E32`, com bordas `#253048`. Esses tons de apoio mantêm o contraste sem substituir as cores da marca. Títulos e navegação usam Montserrat; textos usam Inter. Os arquivos WOFF2 variáveis, com cobertura latina e latina estendida, estão incluídos em `office/static/office/fonts/`. A interface carrega essas fontes do próprio aplicativo, inclusive sem acesso ao Google, e usa fontes do sistema se o carregamento falhar. Os SVGs de assinatura declaram Montserrat com alternativa Arial e não carregam recursos externos.

As fontes foram obtidas do serviço oficial [Google Fonts](https://fonts.google.com/), pelos assets `fonts.gstatic.com` apresentados no [CSS oficial da seleção](https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Montserrat:wght@600;700&display=swap). Os arquivos originais de licença SIL Open Font License acompanham a distribuição como `Inter-OFL.txt` e `Montserrat-OFL.txt`, obtidos do repositório oficial [Inter](https://github.com/google/fonts/blob/main/ofl/inter/OFL.txt) e [Montserrat](https://github.com/google/fonts/blob/main/ofl/montserrat/OFL.txt). Inter cobre os pesos 400, 500, 600 e 700; Montserrat cobre 600 e 700, com os demais pesos suportados pelos arquivos variáveis.

## Variações vetoriais

Os arquivos ficam em `office/static/office/brand/`:

- `logo-horizontal.svg`: assinatura colorida com texto claro, para fundos escuros.
- `logo-horizontal-dark.svg`: assinatura colorida com texto azul-marinho, para fundos claros.
- `logo-monochrome.svg`: assinatura inteiramente clara, para aplicação em uma cor sobre fundo escuro.
- `icon.svg`: símbolo compacto com fita N, órbita e estrela, sem texto.

Esses SVGs são recriações vetoriais limpas derivadas da imagem de referência e do símbolo `brand-mark.svg` usado na aplicação. Não são o arquivo-matriz original da marca nem uma conversão exata de curvas tipográficas. A tipografia usa elementos de texto e pode variar conforme as fontes instaladas. Para produção gráfica que exija reprodução idêntica, substituir pelas matrizes originais com texto convertido em curvas.

Preservar proporções e contraste. Usar o arquivo com texto claro em fundos escuros e o arquivo com texto escuro em fundos claros. Evitar sombras novas, distorção, recorte do símbolo e versões com pouco contraste. A identidade particular de cada empresa continua configurável no escritório.

## Personagens

Os personagens são robôs originais em pixel art, com visor escuro, capacete claro e headset. Seguem os papéis e cores da referência: Atlas azul, Radar verde, Elo laranja, Luma roxo, Cora rosa e Nexo ciano. Pixel e Byte ampliam a equipe técnica com variações da mesma linguagem. O sprite vetorial está em `office/static/office/characters.svg` e oferece os símbolos `avatar-0` até `avatar-7`.

O cenário `office-scene.svg` é uma ilustração decorativa estática. Não indica que um agente está executando trabalho. O estado de execução é apresentado pelos registros reais da aplicação.

## Registro da exploração com geração de imagem

Foi usada a ferramenta integrada ImageGen com a referência visual do manual. Orientação de geração registrada: criar uma variação horizontal em fundo transparente da assinatura NABIO Office AI, mantendo o N em fita dobrada, a transição ciano e roxo, a estrela, a órbita e o texto claro.

O resultado exploratório está em `office/static/office/brand/logo-exploracao.png`. Esse arquivo é um estudo raster; apresenta artefatos de transparência e não é a identidade padrão da interface. As variações SVG acima são os arquivos destinados à aplicação.
