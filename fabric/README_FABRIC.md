# Despliegue en Microsoft Fabric

Pasos exactos para reproducir el pipeline en una capacidad **Trial**. Tiempo
aproximado: 20-30 minutos.

---

## 0. Acceso

1. Entrar en <https://app.fabric.microsoft.com> con el usuario facilitado.
2. Si pide configurar el telefono / MFA adicional, pulsar **Omitir configuración**
   (la prueba no lo requiere).
3. Arriba a la derecha, **Account manager > Start trial** y confirmar. Debe
   quedar activa la **Microsoft Fabric (Preview) Trial**, 60 días.

## 1. Workspace y Lakehouse

1. **Workspaces > New workspace** → nombre `ws-grenergy-dayahead`.
   En *Advanced*, seleccionar la licencia **Trial**.
2. Dentro del workspace: **New item > Lakehouse** → `lh_energy_markets`.

> **Nota sobre la cuenta de prueba**: en el tenant facilitado para esta prueba,
> el administrador tiene deshabilitada la creación de áreas de trabajo
> ("Creación de áreas de trabajo de la aplicación deshabilitada"), así que todo
> se ha desplegado sobre **Mi área de trabajo**. Es funcionalmente equivalente
> para este caso —notebook, Spark, pipeline y programación funcionan igual—; lo
> único que no está disponible ahí es la integración con Git, que este diseño no
> necesita porque el notebook se descarga el código del repositorio por sí mismo.

## 2. El código: no hay que subir nada

El notebook no lleva la lógica dentro (importa el paquete `dayahead`, el mismo
que se ejecuta y se testea en local), pero **se trae el código él solo**. La
celda de *bootstrap* prueba tres orígenes en orden:

1. `Files/src` del Lakehouse, si está subido o si el workspace usa Git
   integration.
2. **Clonado del repositorio de GitHub** — basta con poner la URL en `REPO_URL`.
3. Configuración embebida en el propio notebook, si solo falta el YAML.

Por eso el camino corto es publicar el repo en GitHub y poner su URL: **no hace
falta subir carpetas a mano**.

> Si se prefiere no depender del repo, sigue valiendo la vía manual: Lakehouse →
> pestaña **Files** → **Upload > Upload folder**, subiendo `src/` y `config/`
> para que queden como `Files/src/dayahead/...` y `Files/config/sources.yaml`.
>
> Para un entorno real, lo correcto es **Workspace settings > Git integration**,
> que sincroniza el código en cada push.

## 3. Notebook

1. Desde el **workspace** (no desde dentro del Lakehouse): barra superior →
   **Importar > Cuaderno > Desde este equipo** → subir
   `fabric/notebooks/nb_ingest_dayahead.ipynb`.

   > Hay dos versiones del mismo notebook: el `.ipynb` es el que acepta el
   > importador de Fabric sin problemas; el `.py` es el formato *Fabric notebook
   > source*, útil para revisar los cambios en el repositorio.
2. Con el notebook abierto, en el panel izquierdo **Lakehouses > Add** →
   seleccionar `lh_energy_markets` como *default lakehouse*.
3. En la celda `REPO_URL`, sustituir `<TU-USUARIO>` por el usuario de GitHub
   donde esté publicado el repositorio.
4. Localizar la celda de parámetros (`countries`, `lookback_days`…) y marcarla:
   menú `...` de la celda → **Toggle parameter cell**.
5. Configurar el token de ENTSO-E, en orden de preferencia:
   - **Key Vault**: crear el secreto `entsoe-security-token` y ajustar la URL
     del vault en la celda `load_entsoe_token()`.
   - **Sin Key Vault** (suficiente para la Trial): en esa misma celda,
     sustituir el `os.getenv(...)` por el token, o definir la variable de
     entorno en el *environment* de Spark del workspace.
6. **Run all**. La primera ejecución tarda ~2-3 minutos (arranque del pool Spark).

Al terminar deben existir cinco tablas en el Lakehouse:

| Tabla                 | Contenido                          | Filas/día |
|-----------------------|------------------------------------|-----------|
| `dayahead_prices_es`  | España, PT15M, EUR                 | 96        |
| `dayahead_prices_ro`  | Rumanía, PT15M, EUR                | 96        |
| `dayahead_prices_de`  | Alemania, PT60M, EUR               | 24        |
| `dayahead_prices_pl`  | Polonia, PT15M, PLN→EUR            | 96        |
| `etl_run_log`         | Traza de cada ejecución            | 1         |

Comprobación rápida desde el **SQL analytics endpoint** del Lakehouse:

```sql
SELECT delivery_date, COUNT(*) AS puntos, ROUND(AVG(price_eur), 2) AS media_eur
FROM dayahead_prices_es
GROUP BY delivery_date
ORDER BY delivery_date DESC;
```

## 4. Pipeline y planificación

1. **New item > Data pipeline** → `pl_dayahead_daily`.
2. Añadir una actividad **Notebook** apuntando a `nb_ingest_dayahead`.
3. En **Settings > Base parameters**, declarar: `countries`, `lookback_days`,
   `horizon_days`, `date_from`, `date_to`
   (valores por defecto en `fabric/pipelines/pl_dayahead_daily.json`).
4. **Retry: 2**, intervalo 300 s, timeout 1 h.
5. **Schedule**: diario a las **14:30 (Romance Standard Time)**.

   La hora no es arbitraria: la subasta Day Ahead de SDAC casa entre las 12:00
   y las 13:00 CET, y el BCE publica el fixing PLN/EUR a las 14:15 CET.
   Ejecutar a las 14:30 asegura que están disponibles tanto los precios de D+1
   como el tipo de cambio del día.

El JSON de `fabric/pipelines/pl_dayahead_daily.json` sirve de referencia de la
configuración; los GUID de workspace y notebook son propios de cada tenant y
hay que rellenarlos si se importa en lugar de crearlo por la UI.

## 5. Recarga histórica

El pipeline admite backfill sin tocar nada: se ejecuta a mano con parámetros.

```
date_from = 2026-01-01
date_to   = 2026-06-30
countries = ES,RO,DE,PL
```

Como la escritura es un `MERGE` sobre `(country_code, ts_utc, resolution)`,
puede repetirse tantas veces como haga falta sin duplicar ni una fila.

## 6. Añadir un país nuevo

Sin tocar el notebook ni el pipeline:

1. Añadir el bloque del país en `config/sources.yaml` y volver a subir el
   fichero a `Files/config/`.
2. Si la fuente usa una API ya soportada (ENTSO-E cubre casi toda Europa),
   basta con el nuevo `domain` EIC.
3. Si es un proveedor nuevo, crear un conector en
   `src/dayahead/connectors/` decorado con `@register("nombre")`.
4. Añadir el código del país al parámetro `countries` del pipeline.

## 7. Capturas para la entrega

Conviene adjuntar al repositorio (`docs/img/`) al menos:

- El Lakehouse con las cinco tablas creadas.
- El notebook ejecutado con la salida JSON del resumen.
- El pipeline con su schedule configurado.
- Una ejecución correcta en **Monitoring hub**.
