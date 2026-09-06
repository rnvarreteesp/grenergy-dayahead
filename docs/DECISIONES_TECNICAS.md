# Decisiones técnicas

Este documento explica **por qué** el pipeline está construido así. Cada
apartado plantea el problema real que se encontró, la decisión tomada y qué se
descartó.

---

## 1. Las cuatro APIs no se parecen en nada

El enunciado describe cuatro fuentes con formatos, autenticación, monedas y
granularidades distintas. Al integrarlas aparecieron tres comportamientos que
no están documentados en el enunciado y que, de no tratarse, producen datos
silenciosamente incorrectos.

### 1.1 ENTSO-E devuelve varias subastas mezcladas

Una petición `documentType=A44` para España devuelve **siete `TimeSeries` para
dos días**, no una por día. Se distinguen por `contract_MarketAgreement.type`:

- `A01` — subasta **diaria**: es el Day Ahead que se pide.
- `A07` — subastas **intradiarias**, con precios distintos para las mismas horas.

Sin filtrar, las series se pisan entre sí y la tabla acaba con precios que no
son Day Ahead. El filtro es configurable
(`contract_market_agreement_type: A01`), no está escrito en el código, porque
no todos los mercados publican el mismo conjunto de subastas: Rumanía, por
ejemplo, solo devuelve `A01`.

### 1.2 La curva de precios viene con huecos deliberados

Las respuestas usan `curveType` **A03** (*variable sized block*): un punto es
válido **hasta que aparece el siguiente**, de modo que las posiciones repetidas
no se publican. En un día real de España llegaron **85 de 96 posiciones**.

Insertar tal cual habría dejado 11 cuartos de hora vacíos cada día, que el
control de huecos habría reportado como error de la fuente cuando en realidad
es el formato. El conector reconstruye la curva rellenando hacia delante hasta
completar las posiciones del `Period`.

### 1.3 SMARD publica bloques semanales, no rangos

La API alemana no admite filtro por fechas: hay un índice de *timestamps*
(inicio de cada semana) y un fichero por semana con 168 puntos horarios. Para
un rango arbitrario hay que localizar el bloque **que ya estaba abierto** antes
del inicio del rango, descargar los que solapan, y recortar después. Las horas
aún no publicadas llegan como `null` y se descartan en lugar de escribirse como
ceros.

### 1.4 PSE marca el final del intervalo

En la respuesta de PSE, el registro con `dtime_utc = 22:15` corresponde al
periodo `22:00 - 22:15`, es decir, **el timestamp es el fin del intervalo**. Los
otros tres mercados publican el inicio.

Mezclar ambas convenciones desplaza toda la serie polaca 15 minutos y arruina
cualquier comparación entre países. El conector resta la duración del intervalo
y deja **todo el sistema con una única convención: `ts_utc` es siempre el
inicio**. Está declarado en el YAML (`timestamp_marks: interval_end`) porque es
una característica de la fuente, no del código.

---

## 2. Normalización de tiempo

Todo se almacena en **UTC** (`ts_utc`), pero se guarda además `ts_local` y
`delivery_date` en hora local del mercado. Las tres columnas hacen falta:

- **UTC** es lo único que permite comparar cuatro países en el mismo eje: cuando
  en España son las 14:00 (CEST, UTC+2) en Rumanía son las 15:00 (EEST, UTC+3),
  y es el mismo instante de mercado.
- La **fecha local** es la unidad de negocio: el "día D" de una subasta va de
  00:00 a 00:00 *hora local*, que en UTC es 22:00Z o 23:00Z según la época del
  año. Particionar y filtrar por `delivery_date` es lo que espera cualquiera
  que consulte los datos.

### El cambio de hora no es un caso extremo, es un caso obligatorio

Dos días al año el día local no tiene 24 horas. Un contador fijo de 96 puntos
daría una falsa alarma de hueco en marzo y descartaría datos en octubre:

| Día | Horas locales | Puntos PT15M | Puntos PT60M |
|-----|---------------|--------------|--------------|
| 29/03/2026 (entra el horario de verano) | 23 | **92** | **23** |
| Día normal | 24 | 96 | 24 |
| 25/10/2026 (sale el horario de verano) | 25 | **100** | **25** |

`transform.expected_points()` lo calcula con `zoneinfo` a partir de la
diferencia real en UTC entre dos medianoches locales consecutivas, en lugar de
asumir 24 horas. Se comprobó contra el 29/03/2026 real de las cuatro APIs: los
cuatro países devolvieron exactamente los puntos esperados.

---

## 3. Conversión PLN → EUR

**Fuente**: tipo de referencia diario del **BCE** (serie `EXR.D.PLN.EUR.SP00.A`).

**Por qué el BCE y no un conversor cualquiera**: es la referencia pública,
gratuita, sin autenticación, auditable y la que usan de facto los informes de
mercado europeos. La alternativa natural era el NBP (banco central polaco), que
también publica un tipo oficial; se prefirió el BCE por coherencia — el resto
del dato ya está en euros y el reporting del grupo es en euros.

**Qué tipo se aplica**: el del **día de entrega en hora local**, no el del
momento de la ingesta. Esto es lo que hace que una recarga histórica devuelva
exactamente los mismos números que la carga original. Si se usara el tipo del
día de ejecución, cada backfill reescribiría precios distintos y la tabla
dejaría de ser reproducible.

**Fines de semana y festivos**: el BCE no publica esos días, pero el mercado
eléctrico sí. Se arrastra el último tipo hábil publicado (*forward fill*), que
es la convención habitual, con una ventana de seguridad de 10 días. Si tampoco
hay tipo en esa ventana, el proceso falla de forma explícita en lugar de
inventar un valor.

**Trazabilidad**: se conservan `price_original`, `currency_original` y
`fx_rate`, de modo que cualquier precio en euros se puede reconstruir y
auditar. Convertir destruyendo el original habría hecho imposible verificar la
conversión más adelante.

---

## 4. Diseño del pipeline

### 4.1 Configuración declarativa, no código

`config/sources.yaml` describe cada país: conector, zona horaria, resolución,
moneda, tabla destino y parámetros propios de la API. El orquestador no contiene
ningún valor específico de un país.

El requisito del enunciado — *"incorporar nuevos países sin reescribir el
pipeline"* — se cumple así: dar de alta Francia o Portugal es añadir un bloque
YAML con su código EIC. Solo hace falta escribir código si aparece un
**protocolo** nuevo, y en ese caso es una subclase de `Connector` con
`@register("nombre")`; el registro la descubre sola.

### 4.2 Contrato único entre fuentes y almacenamiento

Todos los conectores devuelven el mismo objeto (`PricePoint`) y todos los
*sinks* aceptan ese objeto. Consecuencias prácticas:

- La misma lógica de ingesta corre en local (DuckDB) y en Fabric (Delta). El
  notebook de Fabric **no duplica código**: importa el paquete y cambia el sink.
- Los tests se ejecutan sin Spark y sin red, en un segundo.

### 4.3 Idempotencia por diseño

La escritura es un `MERGE` sobre la clave natural
`(country_code, ts_utc, resolution)`, nunca un `append`.

Esto no es un adorno: es lo que permite que la ventana de ejecución sea
**D-2 .. D+1** en lugar de solo D+1. Al reprocesar los dos días anteriores en
cada ejecución, el pipeline **se autorrepara**: un día que quedó incompleto
porque la API estaba caída se completa en la siguiente ejecución, sin
intervención manual y sin duplicar ni una fila. ENTSO-E además revisa precios
publicados, y el `MERGE` los actualiza en sitio.

### 4.4 Aislamiento de fallos

Cada país se ingesta dentro de su propio `try`. Que ENTSO-E esté caído no puede
impedir que se cargue Alemania. Los fallos se acumulan, se reportan juntos al
final y hacen fallar la ejecución del pipeline, pero solo después de haber
cargado todo lo que sí estaba disponible.

Sobre esto, `HttpClient` reintenta con *backoff* exponencial ante 429 y 5xx,
que son los errores transitorios típicos de estas APIs públicas.

### 4.5 Calidad del dato como parte del pipeline

El enunciado pide carga *"sin huecos"*, así que la detección de huecos no es un
extra: cada ejecución compara los puntos recibidos con los esperados por día
(ajustados por DST) y deja el resultado en el log y en la tabla
`etl_run_log`. Con `--fail-on-gaps` el proceso termina en error, para poder
enganchar una alerta en el pipeline.

### 4.6 Por qué una tabla por país

Lo pide el enunciado, y además tiene sentido operativo: aísla los reprocesos
(recargar Polonia no toca España), permite políticas de retención distintas y
evita que el volumen de un mercado 15-minutal penalice las consultas de uno
horario. El esquema es idéntico en las cuatro, así que la unión para comparar
es trivial y se hace en la capa de lectura.

---

## 5. Seguridad de la API

**Mecanismo elegido: API key de larga duración → JWT HS256 de vida corta**
(`Authorization: Bearer`), con expiración de 60 minutos y *scope* `prices:read`.

**Por qué**:

- **Sin estado en servidor.** No hay sesiones que compartir entre réplicas: la
  API escala horizontalmente detrás de un balanceador sin almacén de sesión.
- **La credencial de larga duración viaja una sola vez**, no en cada petición.
  Una API key en cada llamada multiplica la superficie de exposición (logs,
  cabeceras, historiales de proxy).
- **Revocación y rotación por cliente**: cada consumidor tiene su clave; se
  revoca sin afectar a los demás. El token corto limita la ventana de daño si
  se filtra.
- **La interfaz web no toca la credencial permanente**: guarda el token en
  memoria de sesión.
- **Claims estándar** (`sub`, `scope`, `exp`, `iss`) — la puerta natural a
  autorización por país o por rango si más adelante hace falta.

**Qué se descartó**:

- *API key en cada petición*: más simple, pero sin expiración, sin scopes y con
  el secreto permanente circulando constantemente.
- *HTTP Basic*: envía la credencial en cada llamada y no aporta nada frente a lo
  anterior.
- *OAuth2 completo con Entra ID*: **es lo que se usaría en producción** en un
  entorno Azure/Fabric, delegando la emisión y validando contra JWKS. Se
  descartó aquí porque exige un tenant y una app registration que no forman
  parte del alcance de la prueba. Precisamente por eso toda la lógica está
  aislada en `src/api/security.py`: sustituir `require_token` por una
  validación JWKS no toca ni un endpoint.

**Medidas complementarias**: CORS restringido por configuración, limitación de
peticiones (120/min por cliente), y validación estricta de parámetros con
Pydantic incluido un tope de 366 días por consulta para evitar consultas
degenerativas.

---

## 6. Diferencias de granularidad en la interfaz

Alemania publica en **PT60M** y los otros tres en **PT15M**. Pintarlos juntos
sin tratamiento da una gráfica engañosa: la serie alemana parece más "plana"
solo porque tiene cuatro veces menos puntos.

La API acepta un parámetro `granularity`:

- **Sin parámetro**: cada país en su resolución nativa (fidelidad máxima).
- **`PT60M`**: los mercados de 15 minutos se agregan por **media aritmética**
  dentro de cada hora. Es correcto porque los cuatro intervalos duran lo mismo,
  así que la media simple equivale a la media ponderada por tiempo, y es el
  precio horario equivalente que se usa habitualmente para comparar mercados.
- **`PT15M`**: se respeta el dato nativo; Alemania sigue en horario porque
  interpolar precios de subasta inventaría datos que no existen.

La agregación se hace **en la base de datos** (`time_bucket` de DuckDB), no en
el navegador: la interfaz recibe menos datos y la misma lógica sirve a
cualquier otro consumidor de la API.

---

## 7. Qué haría distinto con más tiempo

- **Capa medallion completa**: hoy los conectores normalizan en memoria y
  escriben directamente la tabla final. Guardar además la respuesta cruda
  (bronze) permitiría reprocesar sin volver a llamar a las APIs, que es
  importante porque ENTSO-E limita el histórico consultable.
- **Autenticación con Entra ID** en lugar de JWT propio, como se explica arriba.
- **Alertas con Data Activator** sobre `etl_run_log` en vez del correo del
  pipeline.
- **Tests de contrato** contra las APIs reales, ejecutados a diario y separados
  de la suite unitaria, para detectar cambios de formato en origen.
- **Detección de anomalías de precio** (saltos imposibles, precios idénticos
  repetidos), que en mercados eléctricos suele delatar un problema de la fuente
  antes que un hueco.
