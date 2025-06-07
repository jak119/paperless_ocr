<#
.SYNOPSIS
  Imports environment variables from a .env file to Azure Key Vault.
.DESCRIPTION
  Reads key-value pairs from a .env file and creates/updates secrets in Azure Key Vault.
.PARAMETER EnvFilePath
  Path to the .env file
.PARAMETER KeyVaultName
  Name of the Azure Key Vault
#>

param(
  [Parameter(Mandatory = $true)]
  [string]$EnvFilePath,

  [Parameter(Mandatory = $true)]
  [string]$KeyVaultName
)

# Verify the .env file exists
if (-not (Test-Path $EnvFilePath)) {
  throw "Environment file not found at path: $EnvFilePath"
}

# Verify Azure connection
try {
  Get-AzContext -ErrorAction Stop
}
catch {
  throw "Not connected to Azure. Please run Connect-AzAccount first."
}

# Read and parse the .env file
$envContent = Get-Content $EnvFilePath

foreach ($line in $envContent) {
  # Skip empty lines and comments
  if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) {
    continue
  }

  # Split the line into key and value
  $keyValue = $line.Split('=', 2)
  if ($keyValue.Length -eq 2) {
    $key = $keyValue[0].Trim()
    $value = $keyValue[1].Trim()

    # Remove quotes if present
    $value = $value -replace '^["'']|["'']$'

    try {
      # Convert key to valid secret name (alphanumeric and dashes only)
      $secretName = $key -replace '[^a-zA-Z0-9-]', '-'
      
      # Set the secret in Key Vault
      $secureValue = ConvertTo-SecureString -String $value -AsPlainText -Force
      Set-AzKeyVaultSecret -VaultName $KeyVaultName -Name $secretName -SecretValue $secureValue
      Write-Host "Successfully set secret: $secretName"
    }
    catch {
      Write-Error "Failed to set secret $secretName : $_"
    }
  }
}

Write-Host "Environment file import completed."