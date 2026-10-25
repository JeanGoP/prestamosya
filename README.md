# PrestaYa

Sistema de préstamos con clientes, fotografía, planes semanales/quincenales/mensuales, registro de abonos y contratos firmados. El backend, la interfaz, la base SQLite, las fotografías y los PDF se ejecutan en un solo servicio Docker.

## Cálculo

Se utiliza **interés simple**, con tasa predeterminada de **10% mensual**, editable en cada préstamo. Un mes del plan equivale a 30 días.

`interés = capital × (tasa mensual / 100) × meses`

Por ejemplo, $200.000 al 10%: un mes suma $220.000; dos meses suman $240.000. El total se reparte en cuotas tan iguales como permitan los pesos enteros. El vencimiento final ocurre exactamente al cumplir 30 × meses días. Los abonos parciales se asignan primero a la cuota pendiente más antigua.

## Desarrollo local

1. Copie `.env.example` a `.env` y configure `FLASK_SECRET_KEY` y `ADMIN_PASSWORD` con valores largos y únicos. Para HTTP local, configure `COOKIE_SECURE=false`.
2. Ejecute `docker compose -f compose.yaml -f compose.dev.yaml up --build`.
3. Abra `http://localhost:8000` e ingrese con `ADMIN_USERNAME` (por defecto `admin`) y `ADMIN_PASSWORD`.

El usuario administrador se actualiza con los valores de entorno en cada arranque. La ruta `/health` sirve como comprobación de salud.

## Despliegue en Coolify

1. En Coolify cree una **Application** desde el repositorio Git `https://github.com/JeanGoP/prestamosya`, rama `main`, con estrategia de construcción **Docker Compose**. Use `/` como directorio base y `compose.yaml` como ubicación del archivo Compose.
2. Configure `FLASK_SECRET_KEY`, `ADMIN_USERNAME` y `ADMIN_PASSWORD` como variables de entorno. Use valores largos y únicos para las dos credenciales secretas.
3. `SERVICE_URL_PRESTAYA_8000` en `compose.yaml` indica a Coolify que enrute el dominio generado al servicio `prestaya` en el puerto interno `8000`. En **Domains** puede reemplazarlo por un dominio propio HTTPS. Mantenga `COOKIE_SECURE=true` al usar HTTPS. El dominio temporal `sslip.io` generado por Coolify usa HTTP; para probar el inicio de sesión allí configure temporalmente `COOKIE_SECURE=false` y vuelva a `true` cuando tenga HTTPS.
4. Despliegue y compruebe que `/health` devuelve `{"status":"ok"}` antes de ingresar. No active **Raw Compose**, porque Coolify debe configurar el proxy. Si aparece `404 page not found`, revise que el dominio esté asociado al servicio `prestaya` y al puerto `8000`, y que el contenedor figure como saludable.
5. Conserve el volumen `prestaya_data`: contiene `/app/data/prestaya.sqlite3`, `/app/data/uploads` y `/app/data/contracts`. Para respaldar, haga una copia consistente de la base SQLite y guarde también las carpetas de fotos y contratos.

## Contratos y firmas

1. En **Configuración**, complete nombre, cédula, ciudad de expedición, ciudad de domicilio, teléfono del prestamista y forma de pago.
2. En la ficha del cliente, complete ciudad de expedición, ciudad de domicilio y teléfono. Los clientes existentes conservan sus datos anteriores; el sistema añade las columnas nuevas al arrancar.
3. Al crear un préstamo se abre el contrato con la tasa, el capital, el total y las fechas reales de todas las cuotas. Si falta algún dato, el sistema indica cuál y ofrece enlaces para completarlo.
4. El prestamista y el cliente firman en la pantalla táctil o con el mouse. La firma solo se acepta por HTTPS. Se guarda un PDF con ambas firmas, la fecha de firma y hashes SHA-256 en el volumen persistente. El PDF firmado no se regenera al editar los datos ni puede reemplazarse desde la interfaz.
5. Después de firmar, **Ver contrato firmado** abre el PDF guardado dentro del sistema. También puede descargarlo. La vista previa y la descarga requieren iniciar sesión y verifican la integridad del archivo.

La firma dibujada y los hashes son evidencia técnica básica, no un servicio de firma digital certificada ni una verificación independiente de identidad. Revise el texto contractual y la validez del mecanismo de firma con asesoría jurídica antes de usarlo como documento definitivo. La tasa mensual predeterminada de 10% puede superar el límite legal colombiano aplicable; el sistema no calcula el límite de usura por modalidad y fecha.

## Borrar datos de prueba

En **Configuración → Borrar datos**, cree primero una clave de borrado distinta de la contraseña de acceso. Para crearla o cambiarla debe introducir su contraseña actual. Antes de eliminar datos, la pantalla muestra cuántos registros hay y exige escribir `BORRAR TODO` junto con la clave. Cinco claves incorrectas bloquean el borrado durante 15 minutos.

El borrado elimina clientes, préstamos, cuotas, pagos, fotos y contratos firmados; conserva el usuario, los datos del prestamista y la clave de borrado. Es irreversible. Haga un respaldo del volumen `prestaya_data` antes si necesita esos datos.

No requiere otro contenedor de base de datos. SQLite permite mantener todo en el mismo servicio. Para una instalación de alto tráfico o múltiples réplicas se necesitaría migrar a una base de datos servidor.

## Alcance actual

- Alta, edición, búsqueda y ficha de clientes con captura de foto desde el móvil.
- Préstamos de 1 a 36 meses, tasa individual de 0% a 100% mensual y calendario de cuotas.
- Registro de abonos parciales, saldos, cuotas vencidas e historial de pagos.
- Acceso con un usuario administrador y formularios protegidos con token CSRF.
- Contrato en PDF con calendario de cuotas, firmas táctiles de ambas partes, descarga autenticada y comprobación de integridad.

Los importes se registran en pesos colombianos enteros. No hay edición ni eliminación de préstamos o pagos para preservar el historial; las correcciones contables requieren una función de reverso que aún no está implementada.
