# BPMN connector catalog

`connectors.json` describes the operations used by the two Praksa models. It is editor metadata, not a new API or an authorization policy. The engine resolves connector IDs through its server-side `env.DS`; the catalog deliberately contains no deployment URLs or credentials.

`sendgrid` remains the stable connector ID, while the current implementation is EmailJS. Changing that ID would affect legacy models. HTTP payloads, query parameters and response fields retain their existing contracts.

After changing a route or email template, update this catalog and run `make bpmn-sync` then `make bpmn-check` from the parent `fipu-praksa` workspace. Those commands validate the catalog against connector sources and A/B models and copy its snapshot to the standalone Studio. No service is invoked.
