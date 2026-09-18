# Nocturna — web

Web de solo lectura. Consume la API de lectura de FastAPI (`backend/`), servida en
`http://localhost:8000` en local. Nunca llama a Claude ni contiene lógica de análisis.

## Arranque local

Con la API corriendo en el puerto 8000 (ver `backend/README.md`) y con datos sembrados:

```bash
cd backend && NOCTURNA_ALLOW_SEED=1 uv run python scripts/seed_demo.py
```

Instala dependencias:

```bash
pnpm install
```

Copia `env.example` a `.env.local` a mano (los ficheros `.env*` están en la lista `deny`
de permisos del repo, así que Claude Code no puede crearlos ni leerlos):

```bash
cp env.example .env.local
```

Alternativa sin `.env.local`, pasando la variable inline:

```bash
NOCTURNA_API_URL=http://localhost:8000 pnpm dev
```

Arranca el servidor de desarrollo:

```bash
pnpm dev
```

Abre [http://localhost:3000](http://localhost:3000).

## Comandos

```bash
pnpm test    # tests unitarios (vitest) sobre src/lib/
pnpm lint    # eslint
pnpm build   # build de producción
```

## Configuración

`NOCTURNA_API_URL` — URL base de la API de lectura. Se lee solo en el servidor (Server
Components / `fetch`), con SSR dinámico (`export const dynamic = "force-dynamic"` y
`cache: "no-store"`, ver `src/app/page.tsx`), sin ISR: los hallazgos se publican en
bloque de madrugada y no cambian después, así que cachear solo añadiría latencia sin
ahorrar peticiones reales. Nunca lleva el prefijo `NEXT_PUBLIC_`, para que la URL de la
API no acabe en el bundle del navegador. Ver `env.example`.
