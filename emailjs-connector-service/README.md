# EmailJS connector

Express renders the HTML templates in `templates/` with Handlebars and sends them through the EmailJS HTTP API. Run `npm install` and `node server.js` in this directory.

Set `SERVICE_ID`, `TEMPLATE_ID`, `PUBLIC_KEY`, and `PRIVATE_KEY` in the process environment or an ignored `.env` file. The EmailJS template receives `message` (rendered HTML), `to_email`, and `title`. `PORT` defaults to 3000; the Compose configurations explicitly use 8083. Compose maps the host variables `EMAILJS_SERVICE_ID`, `EMAILJS_TEMPLATE_ID`, `EMAILJS_PUBLIC_KEY`, and `EMAILJS_PRIVATE_KEY` to these service variables.

`POST /email?to=<recipient>&template=<name>` accepts JSON template data. Allowed template names are listed in `server.js` and `../bpmn/connectors.json`. PDF URLs are rendered into messages; the service does not download or attach PDF files.

For local development, use the workspace's `make dev`: `MAILPIT_URL` captures email in Mailpit without calling EmailJS. Keep this variable unset in production. Email templates use the faculty logo and app icon hosted by the FIPU Praksa frontend.
