import os
import requests
import time
import logging
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    filename="paperless_azure_ocr.log",
)
logger = logging.getLogger("paperless_azure_ocr")

# Configuration
BATCH_SIZE = 10
CUSTOM_FIELD_ID = 2


def setup_clients(secrets):
    """Initialize clients with provided secrets"""
    document_intelligence_client = DocumentIntelligenceClient(
        endpoint=secrets["AZURE_ENDPOINT"],
        credential=secrets["AZURE_KEY"],
    )

    headers = {
        "Authorization": f"Token {secrets['PAPERLESS_TOKEN']}",
        "Content-Type": "application/json",
        "CF-Access-Client-Id": secrets["CF_ACCESS_CLIENT_ID"],
        "CF-Access-Client-Secret": secrets["CF_ACCESS_CLIENT_SECRET"],
    }

    return document_intelligence_client, headers


def get_custom_field(headers, secrets):
    """
    Get a custom field by its ID from Paperless
    """
    logger.info(f"Fetching custom field with ID: {CUSTOM_FIELD_ID}")
    custom_fields_url = (
        f"{secrets['PAPERLESS_URL']}/api/custom_fields/{CUSTOM_FIELD_ID}/"
    )
    response = requests.get(custom_fields_url, headers=headers)

    if response.status_code != 200:
        logger.error(
            f"Failed to get custom field {CUSTOM_FIELD_ID}: {response.status_code}"
        )
        return None

    return response.json()


def get_count_of_documents_without_azure_ocr(headers, secrets):
    """
    Get the count of documents that don't have the Azure OCR Completed flag set to true
    """
    logger.info("Querying for count of documents without Azure OCR")

    # Get custom field
    azure_ocr_field = get_custom_field(headers, secrets)
    if not azure_ocr_field:
        logger.error("Custom field 'Azure OCR Completed' not found")
        raise Exception("Custom field 'Azure OCR Completed' not found. Exiting script.")

    # Query for count of documents without the custom field set to true
    documents_url = f"{secrets['PAPERLESS_URL']}/api/documents/"
    params = {
        "ordering": "-added",
        "page_size": 1,  # We only need the count
        "custom_field_query": f'["OR",[[{azure_ocr_field["id"]},"exists","false"],[{azure_ocr_field["id"]},"exact","false"]]]',
    }

    response = requests.get(documents_url, headers=headers, params=params)

    if response.status_code != 200:
        logger.error(f"Failed to get documents: {response.status_code}")
        return 0

    total_count = response.json()["count"]
    logger.info(f"Total documents without Azure OCR flag: {total_count}")
    return total_count


def get_documents_without_azure_ocr(headers, secrets):
    """
    Query Paperless for documents that don't have the Azure OCR Completed flag set to true
    """
    logger.info("Querying for documents without Azure OCR")

    # Get custom field
    azure_ocr_field = get_custom_field(headers, secrets)
    if not azure_ocr_field:
        logger.error("Custom field 'Azure OCR Completed' not found")
        raise Exception("Custom field 'Azure OCR Completed' not found. Exiting script.")

    # Query for documents without the custom field set to true
    logger.info(
        f"Using query size of {BATCH_SIZE} to fetch documents without Azure OCR flag"
    )
    documents_url = f"{secrets['PAPERLESS_URL']}/api/documents/"
    params = {
        "ordering": "-added",
        "page_size": BATCH_SIZE,
        "custom_field_query": f'["OR",[[{azure_ocr_field["id"]},"exists","false"],[{azure_ocr_field["id"]},"exact","false"]]]',
    }

    response = requests.get(documents_url, headers=headers, params=params)

    if response.status_code != 200:
        logger.error(f"Failed to get documents: {response.status_code}")
        return []

    documents_to_process = response.json()["results"]
    if not documents_to_process:
        logger.info("No documents found without Azure OCR flag")
        return [], azure_ocr_field["id"]

    logger.info(f"Found {len(documents_to_process)} documents to process")
    return documents_to_process, azure_ocr_field["id"]


def download_document(document_id, headers, secrets):
    """
    Download the document file from Paperless
    """
    download_url = f"{secrets['PAPERLESS_URL']}/api/documents/{document_id}/download/"
    response = requests.get(download_url, headers=headers)

    if response.status_code != 200:
        logger.error(
            f"Failed to download document {document_id}: {response.status_code}"
        )
        return None

    temp_path = f"/tmp/paperless_doc_{document_id}.pdf"
    with open(temp_path, "wb") as f:
        f.write(response.content)

    return temp_path


def process_with_azure_ocr(file_path, document_intelligence_client):
    """
    Process the document with Azure Document Intelligence
    """
    logger.info(f"Processing {file_path} with Azure OCR")

    try:
        with open(file_path, "rb") as f:
            document_bytes = f.read()

        poller = document_intelligence_client.begin_analyze_document(
            model_id="prebuilt-read",
            body=document_bytes,
        )

        result = poller.result()

        # Extract the text content
        content = ""
        for page in result.pages:
            for line in page.lines:
                content += f"{line.content}\n"

        return content
    except Exception as e:
        logger.error(f"Azure OCR processing error: {e}")
        return None


def update_document_content(document_id, content, custom_field_id, headers, secrets):
    """
    Update the content field in Paperless and set the Azure OCR flag
    """
    logger.info(f"Updating document {document_id} with OCR content")

    update_url = f"{secrets['PAPERLESS_URL']}/api/documents/{document_id}/"

    # First get the current document data
    response = requests.get(update_url, headers=headers)

    if response.status_code != 200:
        logger.error(f"Failed to get document {document_id}: {response.status_code}")
        return False

    doc_data = response.json()

    # Update the content field
    doc_data["content"] = content

    # Set the custom field
    custom_fields = []
    field_exists = False

    for field in doc_data.get("custom_fields", []):
        if field["field"] == custom_field_id:
            field_exists = True
            custom_fields.append({"field": custom_field_id, "value": True})
        else:
            custom_fields.append(field)

    if not field_exists:
        custom_fields.append({"field": custom_field_id, "value": True})

    # Update the document
    response = requests.patch(
        update_url,
        headers=headers,
        json={"content": content, "custom_fields": custom_fields},
    )

    if response.status_code not in [200, 202]:
        logger.error(
            f"Failed to update document {document_id}: {response.status_code} - {response.text}"
        )
        return False

    logger.info(f"Successfully updated document {document_id}")
    return True


def cleanup(file_path):
    """
    Remove temporary files
    """
    if file_path and os.path.exists(file_path):
        os.remove(file_path)


def process_documents(secrets):
    """
    Main processing function that accepts secrets as parameter
    """
    logger.info("Starting Paperless-NGX Azure OCR integration script")

    document_intelligence_client, headers = setup_clients(secrets)

    while get_count_of_documents_without_azure_ocr(headers, secrets) > 0:
        try:
            # Get documents without Azure OCR
            documents, custom_field_id = get_documents_without_azure_ocr(
                headers, secrets
            )

            if not documents:
                logger.info("No documents to process")
                return

            # Process each document
            for doc in documents:
                document_id = doc["id"]
                logger.info(f"Processing document {document_id}: {doc['title']}")

                try:
                    # Download the document
                    file_path = download_document(document_id, headers, secrets)
                    if not file_path:
                        logger.error(f"Failed to download document {document_id}")
                        continue

                    # Process with Azure OCR
                    content = process_with_azure_ocr(
                        file_path, document_intelligence_client
                    )
                    if not content:
                        logger.error(
                            f"Failed to process document {document_id} with Azure OCR"
                        )
                        cleanup(file_path)
                        continue

                    # Update the document in Paperless
                    success = update_document_content(
                        document_id, content, custom_field_id, headers, secrets
                    )

                    # Clean up
                    cleanup(file_path)

                    if success:
                        logger.info(f"Successfully processed document {document_id}")
                    else:
                        logger.warning(f"Failed to update document {document_id}")

                    # Sleep briefly to avoid overloading the API
                    time.sleep(1)

                except Exception as e:
                    logger.error(f"Error processing document {document_id}: {e}")

        except Exception as e:
            logger.error(f"Script execution error: {e}")
