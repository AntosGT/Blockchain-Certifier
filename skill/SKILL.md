---
name: prima
description: Usá esta skill cuando la persona quiera certificar, registrar o dejar constancia de un archivo o de los archivos de un proyecto en prima-certified (por ejemplo "certificame esto", "registrá esta propuesta antes de enviarla"), consultar cómo van sus certificaciones o entender cómo verificar un certificado. Explica cómo usar las herramientas prima_* del MCP fragola.
---

# prima: registrar y certificar archivos

## Qué es y qué prueba
prima le da a la persona una prueba de existencia de su archivo: un certificado con sello de tiempo respaldado por Bitcoin. Sirve como evidencia verificable de anterioridad. No prueba autoría, no es un registro de propiedad intelectual y no reemplaza a un abogado ni a un escribano. Si la persona pregunta por temas legales, decile que el certificado es una prueba técnica y que conviene consultar a un profesional. No prometas más de lo que prueba.

## Cómo hablarle a la persona
- Frases cortas y afirmativas. Vocabulario: prueba de existencia, evidencia verificable, integridad verificable, sello de tiempo respaldado por Bitcoin.
- Nunca nombres la tecnología interna, los códigos de estado ni los valores técnicos (huellas, hashes, identificadores). Si una herramienta devuelve un estado en mayúsculas, no lo muestres: usá el texto de `evidence_status` o explicalo con tus palabras.
- Un aviso de servidor ocupado no es un error. Decile que espere unos minutos y vuelva a intentarlo; su información no se perdió.

## Primera vez: conectarse
1. Llamá `prima_conectar`. Le llega un correo con un enlace.
2. Pedile que copie el enlace del correo y te lo pegue. Llamá `prima_confirmar_conexion` con ese enlace. Se hace una sola vez; la clave queda guardada en su PC y no se muestra.
Si una herramienta dice que todavía no está conectada, empezá por acá.

## Registrar un archivo
1. Preguntá en qué proyecto va (el nombre de la carpeta dentro de Certificaciones) si no lo dijo.
2. Llamá `prima_ver_declaracion` y mostrale el texto completo, sin resumirlo. La declaración se acepta en cada registro. Pedile una confirmación explícita. Sin su "sí", no registres. El sistema no deja registrar si antes no se pidió la declaración; ese pedido sirve por una hora, así que si registrás varios archivos seguidos con su aceptación alcanza con pedirla una vez. Si el sistema dice que hay que pedir la declaración de nuevo, pedila otra vez y mostrásela.
3. Llamá `prima_registrar_archivo` con `declaracion_aceptada=true`. El original se copia a `Certificaciones/<proyecto>/` y al servidor solo viaja la información necesaria para el certificado, nunca el archivo.
   Elegí la categoría que mejor calce con el archivo (si no está clara, `documento`): `audio`, `formato_audiovisual`, `codigo`, `propuesta_comercial`, `composicion`, `concepto_creativo`, `dataset`, `diseno`, `documento`, `idea`, `otro`, `fotografia`, `proyecto`, `investigacion`, `guion`, `sistema`, `logica_de_sistema`, `tratamiento`, `version_obra`, `video`. Pasala siempre en `categoria`.
4. Contale que el certificado llega por correo cuando el sello de tiempo se confirma. Suele tardar: la certificación completa puede demorar de tres a cinco horas, según el tráfico de la red de Bitcoin. Pedile que lo guarde en la carpeta `certificados` de ese proyecto.
Para registrar todo lo nuevo de una carpeta, usá `prima_procesar_pendientes` (también necesita la declaración aceptada). Si el archivo ya estaba registrado, la herramienta lo dice y no lo repite.

## Conservar el archivo exacto
Decile siempre a la persona que guarde el archivo tal cual se registró, sin editarlo, renombrarlo con cambios de contenido ni volver a comprimirlo. Para verificar hace falta ese mismo archivo. Si cambia algo y quiere registrar la nueva versión, se registra como un archivo nuevo.

## Límites del servicio gratuito
- Hasta 50 MB por archivo y, para video, hasta 25 MB. No hay límite de duración.
- Si un video se pasa, la herramienta responde "Excediste el límite". Ofrecé `prima_reducir_video`, que lo baja a 480p con ffmpeg y crea un archivo nuevo. Ese archivo reducido es el que se registra y el que hay que conservar; el original en alta calidad no se certifica.
- Estos límites se pueden ampliar con el MCP y una suscripción paga.

## Consultar cómo van
Llamá `prima_estado_registros` con el proyecto. Contale por archivo, en simple, si está registrado, esperando el sello de tiempo o certificado. El certificado final llega por correo.

## Verificar un certificado
Todavía no hay una herramienta de verificación. Si la persona quiere verificar, ayudala con el paquete ZIP que recibió por correo. Trae `certificate.pdf`, la carpeta `proof/` (`evidence.json`, `evidence.sha256` y `proof.ots`) y `technical_guide.txt`. El paquete no incluye su archivo original: tiene que ser el que conservó. Los pasos de esa guía son tres:
1. **Comprobar el registro en Bitcoin.** En https://opentimestamps.org, sección Stamp & Verify, soltar `proof/proof.ots` en la primera caja y `proof/evidence.json` en la segunda. No soltar el documento original ahí: la prueba sella `evidence.json`, que lista sus documentos. El resultado confirma el bloque de Bitcoin que figura en el certificado.
2. **Comprobar la huella de la evidencia.** Calcular el SHA-256 de `proof/evidence.json` y compararlo con el contenido de `proof/evidence.sha256` y con la huella que muestra el certificado.
3. **Comprobar su documento original.** Calcular su SHA-256 y compararlo con el valor `sha256` que figura para ese archivo en `proof/evidence.json`. Si coinciden, ese archivo exacto es el que se registró. En Windows: `certutil -hashfile <archivo> SHA256`. En Mac o Linux: `shasum -a 256 <archivo>`.
Los pasos 2 y 3 los podés hacer vos con los comandos si la persona te da acceso a los archivos; el paso 1 lo hace ella en el sitio. Estos pasos no necesitan acceso a prima. Los paquetes de certificados anteriores pueden tener otros nombres de archivo y una guía distinta: manda la `technical_guide.txt` de ese paquete. No inventes pasos que la guía no tenga. Cuando la persona te pida verificar, ahí sí podés usar estos términos técnicos, porque la guía del paquete ya los usa.

## Qué no hacer
- No registres sin la aceptación explícita de la declaración.
- No registres archivos que la persona no pidió.
- Después de registrar, no muestres la huella SHA-256, el identificador de la evidencia ni ningún código técnico: contale solo el nombre del archivo, el proyecto, la fecha y que el certificado llega por correo.
- No muestres claves, enlaces de acceso ni el contenido del registro local `.prima/`.
- No modifiques, muevas ni borres los originales de la carpeta del proyecto.

