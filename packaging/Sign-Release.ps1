param(
 [Parameter(Mandatory=$true)][string]$Thumbprint,
 [Parameter(Mandatory=$true)][string[]]$Files,
 [string]$TimestampUrl='http://timestamp.digicert.com',
 [string]$SignTool
)
$ErrorActionPreference='Stop'
# Never import a root, mint a self-signed certificate, or disable malware checks.
$thumb=$Thumbprint.Replace(' ','')
$cert=Get-Item ("Cert:\CurrentUser\My\"+$thumb) -ErrorAction Stop
if(!$cert.HasPrivateKey){throw 'Code-signing certificate has no usable private key'}
if($cert.NotBefore -gt (Get-Date) -or $cert.NotAfter -le (Get-Date)){throw 'Certificate is not currently valid'}
if(!($cert.EnhancedKeyUsageList | Where-Object {$_.ObjectId -eq '1.3.6.1.5.5.7.3.3'})){throw 'Certificate is not for code signing'}
$chain=New-Object System.Security.Cryptography.X509Certificates.X509Chain
$chain.ChainPolicy.RevocationMode='Online'
$chain.ChainPolicy.UrlRetrievalTimeout=[TimeSpan]::FromSeconds(15)
try {if(!$chain.Build($cert)){throw ('Certificate trust/revocation check failed: '+($chain.ChainStatus.Status -join ','))}} finally {$chain.Dispose()}
if(!$SignTool){
 $SignTool=(Get-Command signtool.exe -ErrorAction Stop).Source
}
foreach($file in $Files){
 $resolved=(Resolve-Path $file).Path
 & $SignTool sign /sha1 $thumb /s My /fd SHA256 /tr $TimestampUrl /td SHA256 /d 'MusicBox' $resolved
 if($LASTEXITCODE -ne 0){throw "Signing failed: $resolved"}
 & $SignTool verify /pa /all /v $resolved
 if($LASTEXITCODE -ne 0){throw "Signature verification failed: $resolved"}
 $signature=Get-AuthenticodeSignature $resolved
 if($signature.Status -ne 'Valid'){throw "Authenticode status is not Valid: $resolved"}
 [PSCustomObject]@{Path=$resolved;Status=$signature.Status.ToString();Signer=$signature.SignerCertificate.Subject;Timestamped=($null -ne $signature.TimeStamperCertificate)}
}
# Sign application exe/assemblies in dist/MusicBox before rebuilding the Inno
# installer; then sign the resulting installer as a separate final step.
