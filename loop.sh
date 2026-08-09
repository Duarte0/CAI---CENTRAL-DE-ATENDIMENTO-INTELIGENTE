#!/usr/bin/env bash
set -Eeuo pipefail

# Uso:
# ./codex-loop.sh                  Build ilimitado
# ./codex-loop.sh 20               Build por 20 iterações
# ./codex-loop.sh build 20         Build por 20 iterações
# ./codex-loop.sh plan 1
# ./codex-loop.sh specs 1
# ./codex-loop.sh issues 10
#
# Variáveis de ambiente:
#   CODEX_SOL_MODEL / CODEX_TERRA_MODEL / CODEX_LUNA_MODEL
#   CODEX_LOG_DIR         diretório de logs (default: .codex-logs)
#   NO_PROGRESS_LIMIT     iterações sem mudança de git status antes de parar (default: 2)
#   ERROR_LIMIT           falhas consecutivas do `codex exec` antes de parar (default: 3)

SOL_MODEL="${CODEX_SOL_MODEL:-gpt-5.6-sol}"
TERRA_MODEL="${CODEX_TERRA_MODEL:-gpt-5.6-terra}"
LUNA_MODEL="${CODEX_LUNA_MODEL:-gpt-5.6-luna}"

LOG_DIR="${CODEX_LOG_DIR:-.codex-logs}"
NO_PROGRESS_LIMIT="${NO_PROGRESS_LIMIT:-2}"
ERROR_LIMIT="${ERROR_LIMIT:-3}"

ITERATION=0
NO_PROGRESS=0
ERROR_STREAK=0

# Acumuladores de tokens (para o resumo final)
TOTAL_INPUT=0
TOTAL_CACHED=0
TOTAL_OUTPUT=0
TOTAL_REASONING=0

MODE=""
PROMPT_FILE=""
MAX_ITERATIONS=0
MODEL=""
EFFORT="medium"

# ---------- Cores (só se for terminal) ----------
if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
    C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
    C_CYAN=$'\033[36m'; C_MAGENTA=$'\033[35m'
else
    C_RESET=""; C_BOLD=""; C_DIM=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""; C_MAGENTA=""
fi

log()  { printf '%s\n' "$*"; }
info() { printf '%s%s%s\n' "$C_CYAN" "$*" "$C_RESET"; }
ok()   { printf '%s%s%s\n' "$C_GREEN" "$*" "$C_RESET"; }
warn() { printf '%s%s%s\n' "$C_YELLOW" "$*" "$C_RESET"; }
err()  { printf '%s%s%s\n' "$C_RED" "$*" "$C_RESET" >&2; }

fmt_num() {
    # separador de milhar simples, sem depender de locale
    local n="${1:-0}"
    n="${n//[^0-9]/}"   # remove aspas, espaços ou lixo que o jq possa emitir
    [[ -z "$n" ]] && n=0
    printf '%s' "$n" | rev | sed 's/\([0-9]\{3\}\)/\1./g; s/\.$//' | rev
}

fmt_duration() {
    local s="${1:-0}"
    printf '%dm%02ds' "$((s / 60))" "$((s % 60))"
}

case "${1:-}" in
plan)
    MODE="plan"
    PROMPT_FILE="PROMPT_plan.md"
    MAX_ITERATIONS="${2:-1}"
    MODEL="$TERRA_MODEL"
    EFFORT="medium"
    ;;

specs)
    MODE="specs"
    PROMPT_FILE="PROMPT_specs.md"
    MAX_ITERATIONS="${2:-1}"
    MODEL="$TERRA_MODEL"
    EFFORT="medium"
    ;;

issues)
    MODE="issues"
    PROMPT_FILE="PROMPT_issues.md"
    MAX_ITERATIONS="${2:-0}"
    MODEL="$TERRA_MODEL"
    EFFORT="medium"
    ;;

build)
    MODE="build"
    PROMPT_FILE="PROMPT_build.md"
    MAX_ITERATIONS="${2:-0}"
    MODEL="$LUNA_MODEL"
    EFFORT="high"
    ;;

"")
    MODE="build"
    PROMPT_FILE="PROMPT_build.md"
    MAX_ITERATIONS=0
    MODEL="$LUNA_MODEL"
    EFFORT="high"
    ;;

[0-9]*)
    MODE="build"
    PROMPT_FILE="PROMPT_build.md"
    MAX_ITERATIONS="$1"
    MODEL="$LUNA_MODEL"
    EFFORT="high"
    ;;

*)
    err "Modo desconhecido: $1"
    log
    log "Modos válidos:"
    log "  plan | specs | issues | build | <número>"
    exit 1
    ;;
esac

if [[ ! -f "$PROMPT_FILE" ]]; then
    err "Erro: arquivo $PROMPT_FILE não encontrado."
    exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    err "Erro: execute o script dentro de um repositório Git."
    exit 1
fi

if ! command -v codex >/dev/null 2>&1; then
    err "Erro: o comando 'codex' não está instalado ou não está no PATH."
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    err "Erro: 'jq' é necessário para processar a saída --json do codex."
    err "Instale com: sudo apt install jq  (ou brew install jq)"
    exit 1
fi

CURRENT_BRANCH="$(git branch --show-current)"
mkdir -p "$LOG_DIR"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
RUN_LOG_DIR="$LOG_DIR/${MODE}-${RUN_ID}"
mkdir -p "$RUN_LOG_DIR"

SCRIPT_START=$(date +%s)

echo "=========================================="
echo "Modo:       $MODE"
echo "Prompt:     $PROMPT_FILE"
echo "Modelo:     $MODEL"
echo "Potência:   $EFFORT"
echo "Branch:     $CURRENT_BRANCH"
echo "Logs:       $RUN_LOG_DIR/"

if [[ "$MAX_ITERATIONS" -eq 0 ]]; then
    echo "Iterações:  ilimitadas"
else
    echo "Iterações:  $MAX_ITERATIONS"
fi

echo "=========================================="

print_summary() {
    local end_ts elapsed
    end_ts=$(date +%s)
    elapsed=$((end_ts - SCRIPT_START))
    echo
    echo "=========================================="
    ok "Resumo da execução"
    echo "=========================================="
    log "Iterações executadas : $ITERATION"
    log "Duração total         : $(fmt_duration "$elapsed")"
    log "Tokens de entrada     : $(fmt_num "$TOTAL_INPUT")"
    log "Tokens em cache       : $(fmt_num "$TOTAL_CACHED")"
    log "Tokens de saída       : $(fmt_num "$TOTAL_OUTPUT")"
    log "Tokens de raciocínio  : $(fmt_num "$TOTAL_REASONING")"
    log "Total (in+out+reason) : $(fmt_num "$((TOTAL_INPUT + TOTAL_OUTPUT + TOTAL_REASONING))")"
    log "Logs completos em     : $RUN_LOG_DIR/"
    echo "=========================================="
}

trap 'warn "\nInterrompido pelo usuário."; print_summary; exit 130' INT TERM

# Extrai texto legível do stream JSONL do codex e imprime em tempo real.
# OBS: os nomes de campo abaixo (item.text, item.command, etc.) são um
# best-effort baseado no formato conhecido do `codex exec --json`. Se a sua
# versão do codex usar nomes diferentes, rode uma iteração, inspecione
# $RUN_LOG_DIR/iter-N.jsonl com `jq .` e ajuste o filtro abaixo.
render_stream() {
    jq -r '
        .type as $t
        | if $t == "thread.started" then
            "\u001b[2m[thread] \(.thread_id // "?")\u001b[0m"
          elif $t == "item.completed" then
            (.item.type // "item") as $it
            | (.item.text // .item.command // .item.aggregated_output // (.item | tostring)) as $body
            | "\u001b[35m[\($it)]\u001b[0m \($body)"
          elif $t == "turn.completed" then
            "\u001b[2m[turn concluída]\u001b[0m"
          elif $t == "error" then
            "\u001b[31m[erro] \(.message // .error // (. | tostring))\u001b[0m"
          else
            empty
          end
    ' 2>/dev/null || true
}

# Soma os tokens de todos os eventos turn.completed de uma iteração e
# retorna 4 números separados por espaço: input cached output reasoning
extract_usage() {
    local file="$1"
    jq -s '
        [ .[] | select(.type == "turn.completed") | .usage // {} ]
        | reduce .[] as $u (
            {input:0, cached:0, output:0, reasoning:0};
            {
              input:     (.input     + (($u.input_tokens             // $u.input             // 0) | tonumber? // 0)),
              cached:    (.cached    + (($u.cached_input_tokens      // $u.cached_tokens      // $u.cached // 0) | tonumber? // 0)),
              output:    (.output    + (($u.output_tokens            // $u.output            // 0) | tonumber? // 0)),
              reasoning: (.reasoning + (($u.reasoning_output_tokens  // $u.reasoning_tokens   // 0) | tonumber? // 0))
            }
          )
        | "\(.input) \(.cached) \(.output) \(.reasoning)"
    ' "$file" 2>/dev/null || echo "0 0 0 0"
}

while true; do
    if [[ "$MAX_ITERATIONS" -gt 0 && "$ITERATION" -ge "$MAX_ITERATIONS" ]]; then
        info "Limite de iterações alcançado: $MAX_ITERATIONS"
        break
    fi

    echo
    echo "------------------------------------------"
    info "Iniciando iteração $((ITERATION + 1))"
    echo "------------------------------------------"

    BEFORE="$(git status --porcelain)"
    ITER_LOG="$RUN_LOG_DIR/iter-$((ITERATION + 1)).jsonl"
    ITER_START=$(date +%s)

    set +e
    codex exec \
        --dangerously-bypass-approvals-and-sandbox \
        --model "$MODEL" \
        --config "model_reasoning_effort=\"$EFFORT\"" \
        --ephemeral \
        --json \
        - < "$PROMPT_FILE" \
        | tee "$ITER_LOG" \
        | render_stream
    CODEX_EXIT=${PIPESTATUS[0]}
    set -e

    ITERATION=$((ITERATION + 1))
    ITER_END=$(date +%s)
    AFTER="$(git status --porcelain)"

    if [[ "$CODEX_EXIT" -ne 0 ]]; then
        ERROR_STREAK=$((ERROR_STREAK + 1))
        err "codex exec falhou (exit $CODEX_EXIT) na iteração $ITERATION. Falhas seguidas: $ERROR_STREAK/$ERROR_LIMIT"
        if [[ "$ERROR_STREAK" -ge "$ERROR_LIMIT" ]]; then
            err "Parando por excesso de falhas consecutivas do codex."
            print_summary
            exit 1
        fi
        continue
    fi
    ERROR_STREAK=0

    read -r ITER_INPUT ITER_CACHED ITER_OUTPUT ITER_REASONING <<< "$(extract_usage "$ITER_LOG")"
    TOTAL_INPUT=$((TOTAL_INPUT + ITER_INPUT))
    TOTAL_CACHED=$((TOTAL_CACHED + ITER_CACHED))
    TOTAL_OUTPUT=$((TOTAL_OUTPUT + ITER_OUTPUT))
    TOTAL_REASONING=$((TOTAL_REASONING + ITER_REASONING))

    echo
    log "Duração: $(fmt_duration "$((ITER_END - ITER_START))")  |  ${C_DIM}tokens${C_RESET} in=$(fmt_num "$ITER_INPUT") cache=$(fmt_num "$ITER_CACHED") out=$(fmt_num "$ITER_OUTPUT") reasoning=$(fmt_num "$ITER_REASONING")"
    log "${C_DIM}Acumulado${C_RESET} in=$(fmt_num "$TOTAL_INPUT") cache=$(fmt_num "$TOTAL_CACHED") out=$(fmt_num "$TOTAL_OUTPUT") reasoning=$(fmt_num "$TOTAL_REASONING")"

    if [[ "$BEFORE" == "$AFTER" ]]; then
        NO_PROGRESS=$((NO_PROGRESS + 1))
        warn "Iteração $ITERATION sem alterações. Sem progresso: $NO_PROGRESS/$NO_PROGRESS_LIMIT"

        if [[ "$NO_PROGRESS" -ge "$NO_PROGRESS_LIMIT" ]]; then
            warn "Parando por falta de progresso."
            break
        fi
    else
        NO_PROGRESS=0
        # resumo curto do que mudou no working tree
        git diff --stat 2>/dev/null | tail -n 5 || true
    fi

    if [[ "$MODE" == "build" && "$MAX_ITERATIONS" -eq 0 ]]; then
        if [[ -d issues ]]; then
            OPEN_COUNT="$(
                rg -l '^status:\s*open\s*$' issues -g '*.md' 2>/dev/null \
                    | rg -v '0000' \
                    | wc -l
            )"
        else
            OPEN_COUNT=0
        fi

        ok "Iteração $ITERATION concluída. Issues abertas: $OPEN_COUNT"

        if [[ "$OPEN_COUNT" -eq 0 ]]; then
            ok "Todas as issues foram encerradas."
            break
        fi
    else
        ok "Iteração $ITERATION concluída."
    fi
done

print_summary