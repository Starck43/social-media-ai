/**
 * LLM Provider Auto-fill functionality
 * 
 * Automatically fills form fields when provider type is selected based on metadata.
 */

// Metadata will be injected by the template
let LLM_METADATA = window.LLM_METADATA || {};

document.addEventListener('DOMContentLoaded', function() {
	console.log('LLM Provider auto-fill initialized');
	
	const providerTypeField = document.querySelector('select[name="provider_type"]');
	const apiUrlField = document.querySelector('input[name="api_url"]');
	const apiKeyEnvField = document.querySelector('input[name="api_key_env"]');
	const modelNameField = document.querySelector('input[name="model_name"]');
	const nameField = document.querySelector('input[name="name"]');
	
	if (!providerTypeField) {
		console.warn('Provider type field not found');
		return;
	}
	
	// Store original values for comparison
	let isInitialLoad = true;
	
	providerTypeField.addEventListener('change', function() {
		const provider = this.value;
		console.log('Provider type changed to:', provider);
		
		const metadata = LLM_METADATA[provider];
		
		if (metadata) {
			console.log('Found metadata:', metadata);
			
			// Always update API URL when provider changes (except on initial load if field is already filled)
			if (apiUrlField) {
				const shouldUpdate = !isInitialLoad || !apiUrlField.value;
				if (shouldUpdate) {
					apiUrlField.value = metadata.api_url;
					console.log('Set API URL:', metadata.api_url);
				}
			}
			
			// Always update API key env when provider changes (except on initial load if field is already filled)
			if (apiKeyEnvField) {
				const shouldUpdate = !isInitialLoad || !apiKeyEnvField.value;
				if (shouldUpdate) {
					apiKeyEnvField.value = metadata.api_key_env;
					console.log('Set API key env:', metadata.api_key_env);
				}
			}
			
			// Update model name: on initial load only if empty, on user change always suggest first model
			if (modelNameField && metadata.models && metadata.models.length > 0) {
				const shouldUpdate = !isInitialLoad || !modelNameField.value;
				if (shouldUpdate) {
					modelNameField.value = metadata.models[0].id;
					console.log('Set model name:', metadata.models[0].id);
				}
			}
			
			// Auto-fill name only if empty (both initial load and user changes)
			if (nameField && !nameField.value && metadata.display_name) {
				nameField.value = metadata.display_name;
				console.log('Set name:', metadata.display_name);
			}
			
			// Always show available models hint
			if (modelNameField && metadata.models) {
				// Remove old hint
				const oldHint = modelNameField.parentElement.querySelector('.model-hint');
				if (oldHint) oldHint.remove();
				
				// Create new hint
				const hint = document.createElement('small');
				hint.className = 'form-text text-muted model-hint';
				
				const modelsList = metadata.models.map(m => {
					const caps = m.capabilities.join(', ');
					return `<strong>${m.id}</strong> (${caps})`;
				}).join('<br>');
				
				hint.innerHTML = `<strong>Доступные модели:</strong><br>${modelsList}`;
				modelNameField.parentElement.appendChild(hint);
				console.log('Added models hint');
			}
			
			// Mark that initial load is complete
			isInitialLoad = false;
		} else {
			console.log('No metadata found for provider:', provider);
		}
	});
	
	// Trigger change event on page load if provider is already selected (edit mode)
	if (providerTypeField.value) {
		console.log('Provider already selected on load:', providerTypeField.value);
		// Keep isInitialLoad = true for this first trigger
		providerTypeField.dispatchEvent(new Event('change'));
	} else {
		// If no provider selected (create mode), mark as not initial load
		isInitialLoad = false;
	}
});
