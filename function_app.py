import logging
import os

import azure.functions as func
from azure.identity import (
    AzureCliCredential,
    ManagedIdentityCredential,
)
from azure.keyvault.secrets import SecretClient

from update_ocr import (
    process_documents,
    setup_clients,
    get_custom_field,
    download_document,
    process_with_azure_ocr,
    update_document_content,
    cleanup,
)

app = func.FunctionApp()


def get_secrets():
    """Get secrets from Azure Key Vault"""
    is_local_dev = os.environ.get("IS_LOCAL_DEV", "false").lower() == "true"

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
@app.schedule(schedule="0 */36 * * *", arg_name="timer", run_on_startup=True)
def process_paperless_documents(timer: func.TimerRequest) -> None:
    logging.info("Paperless document processing timer trigger function started")

    try:
        secrets = get_secrets()
        process_documents(secrets)
        logging.info("Processing completed successfully")

    except Exception as e:
        logging.error(f"Error in processing: {str(e)}")


@app.function_name(name="ProcessSingleDocument")
@app.route(route="process_document", methods=["POST"])
def process_single_document(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing single document request received")

    try:
        # Get document ID from request body
        req_body = req.get_json()
        document_id = req_body.get("document_id")

        if not document_id:
            return func.HttpResponse(
                "Please provide a document_id in the request body", status_code=400
            )

        # Get secrets and setup clients
        secrets = get_secrets()
        document_intelligence_client, headers = setup_clients(secrets)

        # Get custom field for Azure OCR flag
        azure_ocr_field = get_custom_field(headers, secrets)
        if not azure_ocr_field:
            return func.HttpResponse(
                "Custom field 'Azure OCR Completed' not found", status_code=500
            )

        # Download the document
        file_path = download_document(document_id, headers, secrets)
        if not file_path:
            return func.HttpResponse(
                f"Failed to download document {document_id}", status_code=500
            )

        try:
            # Process with Azure OCR
            content = process_with_azure_ocr(file_path, document_intelligence_client)
            if not content:
                cleanup(file_path)
                return func.HttpResponse(
                    f"Failed to process document {document_id} with Azure OCR",
                    status_code=500,
                )

            # Update the document in Paperless
            success = update_document_content(
                document_id, content, azure_ocr_field["id"], headers, secrets
            )

            # Clean up
            cleanup(file_path)

            if success:
                return func.HttpResponse(
                    f"Successfully processed document {document_id}", status_code=200
                )
            else:
                return func.HttpResponse(
                    f"Failed to update document {document_id} in Paperless",
                    status_code=500,
                )

        except Exception as e:
            cleanup(file_path)
            return func.HttpResponse(
                f"Error processing document: {str(e)}", status_code=500
            )

    except Exception as e:
        return func.HttpResponse(f"Error: {str(e)}", status_code=500)
