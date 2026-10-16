# Jehupi: facturas → Marangatu

App web para la oficina contable que lee facturas de **compras y ventas** (PDF, fotos o escaneos), detecta si son electrónicas o de papel, extrae los datos con IA (**Gemini** o **Claude**), los valida y genera el archivo de importación del Registro de Comprobantes de Marangatu (RG 90/2021).

Funciona en servicios gratuitos y no necesita dominio propio:

| Pieza | Servicio | Costo |
|---|---|---|
| Código | GitHub (repositorio privado) | Gratis |
| App web | Streamlit Community Cloud (`https://….streamlit.app`) | Gratis |
| Base de datos y archivos | Supabase (región São Paulo) | Gratis (500 MB de base, 1 GB de archivos) |
| Lectura de facturas | Gemini (plan gratuito con límites) o Claude (pago por uso) | Según el uso |

## Cómo funciona

1. **Carga:** se elige el cliente, el registro (**COMPRAS** o **VENTAS**) y se suben los archivos. Cada factura se asigna sola al mes de su fecha de emisión.
2. **Clasificación:**
   - KuDE en PDF digital → CDC leído del texto, **sin IA**.
   - Imagen con QR de e-Kuatia → CDC leído del QR, **sin IA**.
   - Preimpresas, autoimpresor, manuales y tickets → los lee la IA.
3. **Facturas electrónicas:** quedan como **EXCLUIDA**, porque ya figuran en Marangatu.
4. **Validación:** DV del RUC, timbrado y vigencia, número, que 10% + 5% + exenta = total, IVA, RUC del cliente y duplicados. Si algo falla, la factura queda en **REVISAR**.
5. **Revisión:** la imagen al lado de los datos, para corregir y aprobar o descartar. Cada cambio queda registrado con usuario y fecha.
6. **Exportación:** genera el ZIP de cada registro (TXT tabulado, UTF-8, sin encabezados) y un Excel de control:
   - compras: `RUCsinDV_REG_MMAAAA_C0001.zip`, 20 campos;
   - ventas: `RUCsinDV_REG_MMAAAA_V0001.zip`, 19 campos.

### Compras y ventas

| | Compras (20 campos) | Ventas (19 campos) |
|---|---|---|
| Tipo de registro | `2` | `1` |
| Contraparte | Proveedor, siempre RUC (`11`) | Comprador: `11` RUC, `12` cédula (sin puntos), `13` pasaporte, `14` cédula extranjera, `15` sin nombre (`44444401`), `16` diplomático, `17` identificación tributaria |
| Razón social (campo 4) | En blanco | En blanco para RUC, cédula y sin nombre; **obligatoria** para los demás |
| Control del cliente | La factura debe estar **a nombre** del cliente | La factura debe estar **emitida por** el cliente |
| Tickets (112) | Campos 9 a 11 en 0, solo el total | Con montos gravados y exentos |
| Columna "no imputa" | Sí | No existe |
| Duplicados | Mismo proveedor, timbrado y número | Mismo timbrado y número |

Si la factura de venta no tiene datos del comprador (consumidor final), se registra como **sin nombre** (`15`, `44444401`).

### Imágenes temporales

Para no llenar el 1 GB gratuito, las imágenes **se borran solas** cuando ya no hacen falta:

- al **aprobar** o **descartar** la factura;
- las que quedaron **OK** o **electrónicas**, al **generar el ZIP** del período;
- las hojas en blanco o que no son comprobantes, enseguida.

Se conservan los **datos** de todas las facturas, no las imágenes.

## Instalación (una sola vez)

### 1. Supabase (base de datos)

1. Crear una cuenta en https://supabase.com y un **proyecto nuevo**:
   - región **South America (São Paulo)**;
   - anotar la **contraseña de la base**.
2. En el proyecto: **SQL Editor** → **New query** → pegar todo el contenido de [`supabase/esquema.sql`](supabase/esquema.sql) → **Run**. Crea las tablas y la carpeta privada `facturas` para las imágenes.
   - **Si ya lo había ejecutado antes de la versión con ventas, ejecútelo de nuevo**: agrega la columna nueva sin borrar datos.
3. Copiar dos datos, que van en los secretos del paso 3:
   - **Connect** (arriba) → **Connection string** → **Session pooler** → URI. Esa es la `DATABASE_URL`: reemplazar `[YOUR-PASSWORD]` por la contraseña de la base.
   - **Project Settings → API**: la **Project URL** (`SUPABASE_URL`) y la clave **service_role** (`SUPABASE_SERVICE_KEY`).

> Usar la conexión **Session pooler**, no la "Direct connection": Streamlit Cloud no se conecta con la directa.

### 2. Key de Gemini

1. Entrar a https://aistudio.google.com → **Get API key** → crear una key.
2. Ver en AI Studio el **nombre del modelo** vigente (por ejemplo, de la familia "Flash"), que va en `GEMINI_MODELO`.

### 3. Publicar en Streamlit Community Cloud

1. Entrar a https://share.streamlit.io con la cuenta de GitHub dueña de este repositorio.
2. **Create app** → elegir el repositorio `coronelstudio-ux/jehupi`, rama `main`, archivo `app.py`.
3. **Advanced settings**:
   - Python **3.12**;
   - en **Secrets**, pegar el contenido de [`.streamlit/secrets.toml.ejemplo`](.streamlit/secrets.toml.ejemplo) con los valores reales.
4. **Deploy**. La primera vez tarda unos minutos.
5. Al abrir la app, crear el **usuario administrador**. Después, desde **Usuarios**, crear uno por persona.
6. **Compartir la app** (botón Share): como el repositorio es privado, la app también lo es. Hay que invitar el correo de cada persona de la oficina. Además, cada una entra con su usuario y contraseña de la app.

## Límites de los planes gratuitos

| Límite | Qué significa | Qué hacer |
|---|---|---|
| Supabase **se pausa tras 7 días sin uso** | La base deja de responder | Con el uso diario no pasa. Si pasa, reactivarla en el panel de Supabase |
| Supabase **no tiene respaldos** en el plan gratuito | Si se borra algo, no hay copia | Descargar periódicamente el Excel de control. El respaldo automático se puede agregar después |
| Streamlit **se duerme tras 12 h sin visitas** | La primera visita del día tarda entre 30 segundos y un minuto | Esperar a que despierte |
| Streamlit tiene **memoria limitada** | Un PDF de cientos de páginas puede cortarse | Subir lotes de **hasta ~100 páginas** |
| Gemini gratuito **limita las solicitudes por minuto** | Lotes grandes van más lentos | La app espera y reintenta sola. Para ir más rápido, subir `HILOS_PARALELOS` o pasar a un plan pago |

## Cambiar de IA

En los **Secrets** de la app:

- Gemini: `PROVEEDOR_IA = "gemini"`, con `GEMINI_API_KEY` y `GEMINI_MODELO`.
- Claude: `PROVEEDOR_IA = "claude"`, con `ANTHROPIC_API_KEY`.

La IA en uso se ve en la barra lateral.

## Probar en una PC (opcional)

```
pip install -r requirements.txt
cp .streamlit/secrets.toml.ejemplo .streamlit/secrets.toml   # completar los valores
streamlit run app.py
```

Pruebas automáticas, con un PostgreSQL de prueba vacío:

```
docker run -d --name pg-pruebas -e POSTGRES_PASSWORD=pruebas -p 5433:5432 postgres:17
TEST_DATABASE_URL=postgresql://postgres:pruebas@localhost:5433/postgres pytest -q
```

## Estructura

```
app.py                  interfaz web
core/
  config.py             configuración (secretos de Streamlit o .env)
  db.py                 base de datos (Supabase / PostgreSQL)
  archivos.py           imágenes temporales (Supabase Storage)
  ingesta.py            PDF/fotos → imagen por página
  qr.py, cdc.py         facturas electrónicas (QR y CDC)
  extractor.py          lectura con IA (Gemini o Claude), con reintentos
  validaciones.py       RUC, timbrado, montos, IVA
  procesador.py         flujo completo de un lote
  exportar.py           ZIP para Marangatu y Excel de control
supabase/esquema.sql    tablas y carpeta de imágenes
tests/                  pruebas automáticas
```

## Pendiente

- **Notas de crédito y débito**, autofacturas y moneda extranjera (Fase 3).
- **Confirmar en Marangatu**: el fin de línea del TXT (hoy CRLF), los montos de los tickets de compras (hoy, campos 9 a 11 en 0) y que acepte la letra del identificador de archivo (`C` compras, `V` ventas).
- **Respaldo automático** de Supabase (GitHub Actions → Google Drive).
