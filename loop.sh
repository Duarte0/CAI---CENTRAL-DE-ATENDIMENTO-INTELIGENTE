#!/usr/bin/env bash
set -Eeuo pipefail

# Uso: ./loop.sh [plan|specs|issues|build|<N>] [max_iterações]
# Modelos por fase: CODEX_PLAN_MODEL, CODEX_SPECS_MODEL, CODEX_ISSUES_MODEL, CODEX_BUILD_MODEL
# Esforço por fase: CODEX_PLAN_EFFORT, CODEX_SPECS_EFFORT, CODEX_ISSUES_EFFORT, CODEX_BUILD_EFFORT
# Gerais: CODEX_TERRA_MODEL, CODEX_LUNA_MODEL, CODEX_LOG_DIR, NO_PROGRESS_LIMIT, ERROR_LIMIT

TERRA_MODEL="${CODEX_TERRA_MODEL:-gpt-5.6-terra}"
LUNA_MODEL="${CODEX_LUNA_MODEL:-gpt-5.6-luna}"

PLAN_MODEL="${CODEX_PLAN_MODEL:-$TERRA_MODEL}"
PLAN_EFFORT="${CODEX_PLAN_EFFORT:-medium}"
SPECS_MODEL="${CODEX_SPECS_MODEL:-$TERRA_MODEL}"
SPECS_EFFORT="${CODEX_SPECS_EFFORT:-medium}"
ISSUES_MODEL="${CODEX_ISSUES_MODEL:-$LUNA_MODEL}"
ISSUES_EFFORT="${CODEX_ISSUES_EFFORT:-high}"
BUILD_MODEL="${CODEX_BUILD_MODEL:-$LUNA_MODEL}"
BUILD_EFFORT="${CODEX_BUILD_EFFORT:-xhigh}"

LOG_DIR="${CODEX_LOG_DIR:-.codex-logs}"
NO_PROGRESS_LIMIT="${NO_PROGRESS_LIMIT:-2}"
ERROR_LIMIT="${ERROR_LIMIT:-3}"

ITERATION=0; NO_PROGRESS=0; ERROR_STREAK=0
TOTAL_INPUT=0; TOTAL_CACHED=0; TOTAL_OUTPUT=0; TOTAL_REASONING=0

if [[ -t 1 ]]; then
    R=$'\033[0m' DIM=$'\033[2m' GRN=$'\033[32m' YLW=$'\033[33m' RED=$'\033[31m' CYN=$'\033[36m'
else
    R=""; DIM=""; GRN=""; YLW=""; RED=""; CYN=""
fi

log()  { printf '%s\n' "$*"; }
info() { printf '%s%s%s\n' "$CYN" "$*" "$R"; }
ok()   { printf '%s%s%s\n' "$GRN" "$*" "$R"; }
warn() { printf '%s%s%s\n' "$YLW" "$*" "$R"; }
err()  { printf '%s%s%s\n' "$RED" "$*" "$R" >&2; }

fmt_num() {
    local n="${1//[^0-9]/}"; [[ -z "$n" ]] && n=0
    printf '%s' "$n" | rev | sed 's/\([0-9]\{3\}\)/\1./g; s/\.$//' | rev
}
fmt_dur() { printf '%dm%02ds' "$(($1/60))" "$(($1%60))"; }

case "${1:-}" in
    plan)   MODE=plan;   PROMPT=PROMPT_plan.md;   MAX="${2:-1}"; MODEL="$PLAN_MODEL";   EFFORT="$PLAN_EFFORT"   ;;
    specs)  MODE=specs;  PROMPT=PROMPT_specs.md;  MAX="${2:-1}"; MODEL="$SPECS_MODEL";  EFFORT="$SPECS_EFFORT"  ;;
    issues) MODE=issues; PROMPT=PROMPT_issues.md; MAX="${2:-0}"; MODEL="$ISSUES_MODEL"; EFFORT="$ISSUES_EFFORT" ;;
    build)  MODE=build;  PROMPT=PROMPT_build.md;  MAX="${2:-0}"; MODEL="$BUILD_MODEL";  EFFORT="$BUILD_EFFORT"  ;;
    "")     MODE=build;  PROMPT=PROMPT_build.md;  MAX=0;         MODEL="$BUILD_MODEL";  EFFORT="$BUILD_EFFORT"  ;;
    [0-9]*) MODE=build;  PROMPT=PROMPT_build.md;  MAX="$1";      MODEL="$BUILD_MODEL";  EFFORT="$BUILD_EFFORT"  ;;
    *) err "Modo inválido: $1. Use: plan | specs | issues | build | <N>"; exit 1 ;;
esac

[[ "$MAX" =~ ^[0-9]+$ ]] || { err "O número máximo de iterações deve ser um inteiro não negativo: $MAX"; exit 1; }
MAX=$((10#$MAX))

[[ -f "$PROMPT" ]]                               || { err "Arquivo não encontrado: $PROMPT"; exit 1; }
git rev-parse --is-inside-work-tree &>/dev/null  || { err "Execute dentro de um repositório Git."; exit 1; }
command -v codex &>/dev/null                     || { err "'codex' não encontrado no PATH."; exit 1; }
command -v jq    &>/dev/null                     || { err "'jq' é necessário. Instale: apt install jq"; exit 1; }

BRANCH="$(git branch --show-current)"
RUN_LOG_DIR="$LOG_DIR/${MODE}-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_LOG_DIR"
START=$(date +%s)

echo "=========================================="
echo "Modo:      $MODE  |  Modelo: $MODEL  |  Esforço: $EFFORT"
echo "Branch:    $BRANCH"
echo "Logs:      $RUN_LOG_DIR/"
echo "Iterações: $( [[ "$MAX" -eq 0 ]] && echo ilimitadas || echo "$MAX" )"
echo "=========================================="

print_summary() {
    local now elapsed
    now=$(date +%s); elapsed=$((now - START))
    echo; echo "=========================================="
    ok "Resumo"
    log "Iterações : $ITERATION  |  Duração: $(fmt_dur $elapsed)"
    local uncached_input visible_output reported_total
    uncached_input=$((TOTAL_INPUT - TOTAL_CACHED)); (( uncached_input < 0 )) && uncached_input=0
    visible_output=$((TOTAL_OUTPUT - TOTAL_REASONING)); (( visible_output < 0 )) && visible_output=0
    reported_total=$((TOTAL_INPUT + TOTAL_OUTPUT))
    log "Tokens    : in=$(fmt_num $TOTAL_INPUT) cache=$(fmt_num $TOTAL_CACHED) out=$(fmt_num $TOTAL_OUTPUT) reason=$(fmt_num $TOTAL_REASONING)"
    log "Detalhes  : entrada_sem_cache=$(fmt_num $uncached_input) saída_visível=$(fmt_num $visible_output)"
    log "Total     : $(fmt_num $reported_total)"
    log "Logs      : $RUN_LOG_DIR/"
    echo "=========================================="
}

extract_usage() {
    # Acumula tokens de todos os eventos turn.completed da execução.
    local file="$1" i=0 c=0 o=0 r=0 line type usage li lc lo lr
    while IFS= read -r line; do
        type=$(printf '%s' "$line" | jq -r '.type // ""' 2>/dev/null) || continue
        [[ "$type" == "turn.completed" ]] || continue
        usage=$(printf '%s' "$line" | jq -r '
            .usage // {} |
            "\(.input_tokens // 0) \(.cached_input_tokens // .cache_read_input_tokens // 0) \(.output_tokens // 0) \(.reasoning_output_tokens // .reasoning_tokens // 0)"
        ' 2>/dev/null) || continue
        read -r li lc lo lr <<< "$usage"
        [[ "$li" =~ ^[0-9]+$ ]] || li=0
        [[ "$lc" =~ ^[0-9]+$ ]] || lc=0
        [[ "$lo" =~ ^[0-9]+$ ]] || lo=0
        [[ "$lr" =~ ^[0-9]+$ ]] || lr=0
        i=$((i + li)); c=$((c + lc)); o=$((o + lo)); r=$((r + lr))
    done < "$file"
    echo "$i $c $o $r"
}

render_stream() {
    jq -r '
        if   .type == "thread.started" then "\u001b[2m[thread] \(.thread_id // "?")\u001b[0m"
        elif .type == "item.completed" then "\u001b[35m[\(.item.type // "item")]\u001b[0m \(.item.text // .item.command // .item.aggregated_output // (.item | tostring))"
        elif .type == "turn.completed" then "\u001b[2m[turn concluída]\u001b[0m"
        elif .type == "error"          then "\u001b[31m[erro] \(.message // .error // (. | tostring))\u001b[0m"
        else empty end
    ' 2>/dev/null || true
}

trap 'warn "\nInterrompido."; print_summary; exit 130' INT TERM

while true; do
    [[ "$MAX" -gt 0 && "$ITERATION" -ge "$MAX" ]] && { info "Limite atingido: $MAX"; break; }

    echo; info "--- Iteração $((ITERATION+1)) ---"

    BEFORE="$(git status --porcelain)"
    BEFORE_HEAD="$(git rev-parse HEAD)"
    ITER_LOG="$RUN_LOG_DIR/iter-$((ITERATION+1)).jsonl"
    ITER_START=$(date +%s)

    set +e
    codex exec --dangerously-bypass-approvals-and-sandbox \
        --model "$MODEL" --config "model_reasoning_effort=\"$EFFORT\"" \
        --ephemeral --json - < "$PROMPT" \
        | tee "$ITER_LOG" | render_stream
    EXIT=${PIPESTATUS[0]}
    set -e

    ITERATION=$((ITERATION+1))
    ITER_END=$(date +%s)
    AFTER="$(git status --porcelain)"
    AFTER_HEAD="$(git rev-parse HEAD)"

    read -r I C O RS <<< "$(extract_usage "$ITER_LOG")"
    TOTAL_INPUT=$((TOTAL_INPUT+I)); TOTAL_CACHED=$((TOTAL_CACHED+C))
    TOTAL_OUTPUT=$((TOTAL_OUTPUT+O)); TOTAL_REASONING=$((TOTAL_REASONING+RS))

    echo
    log "$(fmt_dur $((ITER_END-ITER_START)))  |  ${DIM}in=$(fmt_num $I) cache=$(fmt_num $C) out=$(fmt_num $O) reason=$(fmt_num $RS)${R}"

    if [[ "$EXIT" -ne 0 ]]; then
        ERROR_STREAK=$((ERROR_STREAK+1))
        err "codex falhou (exit $EXIT) — streak: $ERROR_STREAK/$ERROR_LIMIT"
        [[ "$ERROR_STREAK" -ge "$ERROR_LIMIT" ]] && { err "Parando por excesso de falhas."; print_summary; exit 1; }
        continue
    fi
    ERROR_STREAK=0

    LABEL="$(jq -r 'select(.type=="item.completed") | .item.text // ""' "$ITER_LOG" 2>/dev/null \
        | grep -Eo 'BUILD_COMPLETED|BUILD_BLOCKED|BUILD_COMPLETE|ISSUES_COMPLETE|ISSUES_BLOCKED|ISSUE_CREATED' \
        | tail -1 || true)"

    [[ -n "$LABEL" ]] && info "Label: $LABEL"

    case "$LABEL" in
        ISSUES_COMPLETE|BUILD_COMPLETE)  ok "Concluído: $LABEL"; break ;;
        ISSUES_BLOCKED|BUILD_BLOCKED)   warn "Bloqueado: $LABEL"; break ;;
        ISSUE_CREATED) NO_PROGRESS=0; ok "Iteração $ITERATION OK (ISSUE_CREATED)."; continue ;;
    esac

    PROGRESS=0
    [[ "$BEFORE" != "$AFTER" || "$BEFORE_HEAD" != "$AFTER_HEAD" || -n "$LABEL" ]] && PROGRESS=1

    if [[ "$MODE" == "build" ]]; then
        if [[ "$PROGRESS" -eq 0 ]]; then
            NO_PROGRESS=$((NO_PROGRESS+1))
            warn "Sem progresso no git: $NO_PROGRESS/$NO_PROGRESS_LIMIT"
            [[ "$NO_PROGRESS" -ge "$NO_PROGRESS_LIMIT" ]] && { warn "Parando."; break; }
        else
            NO_PROGRESS=0
            if [[ "$BEFORE_HEAD" != "$AFTER_HEAD" ]]; then
                git diff --stat "$BEFORE_HEAD" "$AFTER_HEAD" 2>/dev/null | tail -n 5 || true
            else
                git diff --stat 2>/dev/null | tail -n 5 || true
            fi
        fi
        if [[ "$MAX" -eq 0 && -d issues ]]; then
            OPEN=$(grep -rl '^status:[[:space:]]*open[[:space:]]*$' issues/ --include='*.md' 2>/dev/null | grep -v '0000' | wc -l | tr -d '[:space:]')
            OPEN=${OPEN:-0}
            ok "Iteração $ITERATION OK. Issues abertas: $OPEN"
            [[ "$OPEN" -eq 0 ]] && { ok "Todas as issues encerradas."; break; }
        else
            ok "Iteração $ITERATION OK."
        fi
    else
        if [[ "$PROGRESS" -eq 0 ]]; then
            NO_PROGRESS=$((NO_PROGRESS+1))
            warn "Sem alteração ou label reconhecido: $NO_PROGRESS/$NO_PROGRESS_LIMIT"
            [[ "$NO_PROGRESS" -ge "$NO_PROGRESS_LIMIT" ]] && { warn "Parando."; break; }
        else
            NO_PROGRESS=0
        fi
        ok "Iteração $ITERATION OK."
    fi
done

print_summary
