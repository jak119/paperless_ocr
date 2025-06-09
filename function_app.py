import logging
import os

import azure.functions as func
from azure.identity import (
    AzureCliCredential,
    ManagedIdentityCredential,
)
from azure.keyvault.secrets import SecretClient

from update_ocr import process_documents

app = func.FunctionApp()


def get_secrets():
    """Get secrets from Azure Key Vault"""
    is_local_dev = os.getenv("IS_LOCAL_DEV", "false").lower() == "true"

    if is_local_dev:
        logging.info("Running in local development mode, using Azure CLI credentials")
        credential = AzureCliCredential()
    else:
        logging.info("Running in Azure environment, using Managed Identity credentials")
        credential = ManagedIdentityCredential()

    vault_url = os.environ["vault_url"]
    secret_client = SecretClient(vault_url=vault_url, credential=credential)

    return {
        "PAPERLESS_URL": secret_client.get_secret("PAPERLESS-URL").value,
        "PAPERLESS_TOKEN": secret_client.get_secret("PAPERLESS-TOKEN").value,
        "AZURE_ENDPOINT": secret_client.get_secret("AZURE-ENDPOINT").value,
        "AZURE_KEY": secret_client.get_secret("AZURE-KEY").value,
        "CF_ACCESS_CLIENT_ID": secret_client.get_secret("CF-ACCESS-CLIENT-ID").value,
        "CF_ACCESS_CLIENT_SECRET": secret_client.get_secret(
            "CF-ACCESS-CLIENT-SECRET"
        ).value,
    }


@app.function_name(name="ProcessPaperlessDocuments")
@app.schedule(schedule="0 */4 * * *", arg_name="timer", run_on_startup=True)
def process_paperless_documents(timer: func.TimerRequest) -> None:
    logging.info("Paperless document processing timer trigger function started")

    try:
        secrets = get_secrets()
        process_documents(secrets)
        logging.info("Processing completed successfully")
    except Exception as e:
        logging.error(f"Error in processing: {str(e)}")
        raise
