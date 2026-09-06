[CmdletBinding()]
param(
    [int]$Port = 9334,
    [string]$Session = "gflow-agent-probe",
    [string]$AgentBrowserVersion = "0.27.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSCommandPath
$NpmCacheDir = Join-Path $ProjectRoot ".npm-cache"
$env:npm_config_cache = $NpmCacheDir
$env:AGENT_BROWSER_SOCKET_DIR = Join-Path $ProjectRoot ".agent-browser\sockets"
$env:AGENT_BROWSER_SESSION = $Session

New-Item -ItemType Directory -Force $env:AGENT_BROWSER_SOCKET_DIR | Out-Null

function Invoke-AgentBrowser {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$CommandArgs
    )
    $package = "agent-browser@$AgentBrowserVersion"
    $output = & npx --yes --package $package agent-browser @CommandArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "agent-browser failed with exit code $exitCode for: $($CommandArgs -join ' ')"
    }
    return ($output -join [Environment]::NewLine)
}

function Get-EvalResult {
    param([string]$Expr)
    $json = Invoke-AgentBrowser @("--cdp", "$Port", "--json", "eval", $Expr)
    $parsed = $json | ConvertFrom-Json
    if (-not $parsed.PSObject.Properties['data']) {
        throw "Unexpected agent-browser response (no .data field): $json"
    }
    return $parsed.data.result
}

Write-Host "Probing Agent Mode and Sidebar layout on Flow (CDP port $Port)..."
Write-Host "Please ensure Chrome is running on port $Port and loaded with a Flow project page."
Write-Host ""

# JS script to find Agent Mode toggle buttons, sparkle buttons, switches, and instruction panels in the DOM.
$probeJs = @'
(() => {
  const results = [];
  
  // 1. Search for any buttons containing "Agent" or symbols related to spark
  const elements = Array.from(document.querySelectorAll('button, div[role="button"], span, i, div[role="switch"]'));
  elements.forEach((el, index) => {
    const text = (el.innerText || el.textContent || '').trim();
    const html = el.outerHTML;
    const isSpark = /spark|article_spark|close/i.test(text) || /spark|article_spark|close/i.test(html);
    const hasAgent = /agent/i.test(text);
    
    if (isSpark || hasAgent) {
      results.push({
        index,
        tag: el.tagName,
        text: text.substring(0, 50),
        role: el.getAttribute('role'),
        ariaChecked: el.getAttribute('aria-checked'),
        className: el.className,
        selector: el.id ? `#${el.id}` : `${el.tagName.toLowerCase()}[class="${el.className}"]`
      });
    }
  });

  // 2. Query specific known components
  const closeBtn = document.querySelector("button:has(i.google-symbols:text-is('close'))");
  const agentBtn = document.querySelector("button:has(i.google-symbols:text-is('article_spark'))");
  const addCardBtn = document.querySelector("#instruction-add-card");
  
  return {
    foundElements: results,
    knownSelectors: {
      closeBtnPresent: !!closeBtn,
      agentBtnPresent: !!agentBtn,
      addCardBtnPresent: !!addCardBtn
    }
  };
})()
'@

try {
  $res = Get-EvalResult $probeJs
  Write-Host "=== DOM Probe Results ==="
  Write-Host "Known elements present:"
  Write-Host "  article_spark button (opens sidebar): $($res.knownSelectors.agentBtnPresent)"
  Write-Host "  close button (closes sidebar):         $($res.knownSelectors.closeBtnPresent)"
  Write-Host "  #instruction-add-card button:         $($res.knownSelectors.addCardBtnPresent)"
  Write-Host ""
  Write-Host "Details of matched elements:"
  foreach ($el in $res.foundElements) {
    Write-Host " - Tag: $($el.tag), Text: '$($el.text)', Role: $($el.role), Selector: $($el.selector)"
  }
} catch {
  Write-Error "Failed to probe DOM: $_"
}
