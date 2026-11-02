# BPMN connector catalog

`connectors.json` describes the operations used by the two Praksa models. It is editor metadata, not a new API or an authorization policy. The engine resolves connector IDs through its server-side `env.DS`; the catalog deliberately contains no deployment URLs or credentials.

`emailjs` is the email connector ID in both authoritative models and engine configuration. HTTP payloads, query parameters and response fields retain their existing contracts. Older exported models and browser drafts must select the EmailJS connector before validation or execution.

After changing a route or email template, update this catalog and run `make bpmn-sync` then `make bpmn-check` from the parent `fipu-praksa` workspace. Those commands validate the catalog against connector sources and A/B models and copy its snapshot to the standalone Studio. No service is invoked. Restart running Studio and BPMN MCP processes after catalog changes because they cache the catalog at startup, then reload the model in the browser.
