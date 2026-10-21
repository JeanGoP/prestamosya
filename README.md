# PrestaYa

Sistema de préstamos con clientes, fotografía, planes semanales/quincenales/mensuales y registro de abonos. El backend, la interfaz, la base SQLite y el almacenamiento de fotografías se ejecutan en un solo servicio Docker.

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
3. En **Domains**, asigne un dominio HTTPS al servicio `prestaya` con puerto interno `8000` (por ejemplo, `https://prestamos.ejemplo.com:8000` en el campo de Coolify; el visitante usará HTTPS normal). Mantenga `COOKIE_SECURE=true`.
4. Despliegue y compruebe que `/health` devuelve `{"status":"ok"}` antes de ingresar. No active **Raw Compose**, porque Coolify debe configurar el proxy.
5. Conserve el volumen `prestaya_data`: contiene `/app/data/prestaya.sqlite3` y `/app/data/uploads`. Para respaldar, haga una copia consistente de la base SQLite y guarde también la carpeta de fotos.

No requiere otro contenedor de base de datos. SQLite permite mantener todo en el mismo servicio. Para una instalación de alto tráfico o múltiples réplicas se necesitaría migrar a una base de datos servidor.

## Alcance actual

- Alta, edición, búsqueda y ficha de clientes con captura de foto desde el móvil.
- Préstamos de 1 a 36 meses, tasa individual de 0% a 100% mensual y calendario de cuotas.
- Registro de abonos parciales, saldos, cuotas vencidas e historial de pagos.
- Acceso con un usuario administrador y formularios protegidos con token CSRF.

Los importes se registran en pesos colombianos enteros. No hay edición ni eliminación de préstamos o pagos para preservar el historial; las correcciones contables requieren una función de reverso que aún no está implementada.
