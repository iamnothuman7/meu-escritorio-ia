# Escritório 3D do Nabio

O escritório renderiza modelos e iluminação em WebGL no navegador. O Django continua fornecendo os agentes e seus estados. A cena recebe esses dados, permite girar e aproximar a câmera e devolve a seleção de um agente. As mesas, placas e texturas são geradas no navegador a partir de geometria e fontes locais.

## Build

Com Node.js 24 e npm, execute a partir da raiz do repositório:

```powershell
npm --prefix studio3d ci
npm --prefix studio3d run build
```

O arquivo gerado fica em `office/static/office/studio/scene.js` e entra no fluxo usual de arquivos estáticos do Django. O VPS serve esse arquivo como módulo JavaScript. A execução da cena dispensa servidor Node e conexão com serviços externos.

## Origem e licença

As malhas e animações dos personagens vêm de `src/features/retro-office/objects/agents.tsx` e `src/lib/avatars/profile.ts` do Claw3D. A geometria das divisórias adapta `src/features/retro-office/scene/RemoteOfficeLayoutPreview.tsx` e os utilitários associados. A versão de origem é o [commit 51337f7f](https://github.com/iamlukethedev/Claw3D/tree/51337f7f250459af61a7cf92b7b0015d59faed5c).

Preservamos o [aviso MIT do Claw3D](LICENSE-Claw3D) no código e no bundle. O build também incorpora os avisos das dependências que entram no módulo. Os modelos GLB e as mídias da distribuição Claw3D ficam fora da cena.
