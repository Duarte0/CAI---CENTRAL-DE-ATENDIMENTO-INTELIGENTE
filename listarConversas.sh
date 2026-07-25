#!/bin/bash
# Visualiza o status/resultado de todas as conversas processadas pelo pipeline.
#
# Uso:
#   ./check_conversations.sh              # mostra todas
#   ./check_conversations.sh completed    # só as completadas
#   ./check_conversations.sh failed       # só as que falharam
#   ./check_conversations.sh pending      # só pendentes/processando

set -uo pipefail

API_URL="${API_URL:-http://localhost:8000}"
FILTER="${1:-all}"

# Cores (desativadas automaticamente se a saída não for um terminal)
if [ -t 1 ]; then
    C_RESET='\033[0m'
    C_BOLD='\033[1m'
    C_DIM='\033[2m'
    C_GREEN='\033[32m'
    C_RED='\033[31m'
    C_YELLOW='\033[33m'
    C_CYAN='\033[36m'
    C_BLUE='\033[34m'
else
    C_RESET='' C_BOLD='' C_DIM='' C_GREEN='' C_RED='' C_YELLOW='' C_CYAN='' C_BLUE=''
fi

if ! command -v jq >/dev/null 2>&1; then
    echo -e "${C_RED}❌ 'jq' não está instalado. Instale com: apt install jq${C_RESET}"
    exit 1
fi

echo -e "${C_BOLD}📊 CONVERSAS PROCESSADAS${C_RESET}"
[ "$FILTER" != "all" ] && echo -e "${C_DIM}   (filtro: $FILTER)${C_RESET}"
echo "========================================"
echo ""

CONV_IDS=$(docker compose exec -T redis redis-cli KEYS "ia_result:*" 2>/dev/null | sed 's/^ia_result://' | tr -d '\r')

if [ -z "$CONV_IDS" ]; then
    echo -e "${C_YELLOW}❌ Nenhuma conversa processada ainda${C_RESET}"
    exit 0
fi

TOTAL=0
COUNT_COMPLETED=0
COUNT_FAILED=0
COUNT_OTHER=0

for CONV_ID in $CONV_IDS; do
    STATUS_JSON=$(curl -s --max-time 5 "$API_URL/conversations/$CONV_ID/status" 2>/dev/null)
    if [ -z "$STATUS_JSON" ]; then
        STATUS_VAL="unreachable"
    else
        STATUS_VAL=$(echo "$STATUS_JSON" | jq -r '.status // "unknown"' 2>/dev/null)
        [ -z "$STATUS_VAL" ] && STATUS_VAL="unknown"
    fi

    # Aplica filtro antes de gastar outra chamada de rede
    case "$FILTER" in
        completed) [ "$STATUS_VAL" != "completed" ] && continue ;;
        failed)    [ "$STATUS_VAL" != "failed" ] && continue ;;
        pending)   [ "$STATUS_VAL" = "completed" ] || [ "$STATUS_VAL" = "failed" ] && continue ;;
    esac

    TOTAL=$((TOTAL + 1))
    echo -e "${C_BOLD}🔹 $TOTAL${C_RESET} - ${C_CYAN}$CONV_ID${C_RESET}"

    case "$STATUS_VAL" in
        completed)
            COUNT_COMPLETED=$((COUNT_COMPLETED + 1))
            RESULT=$(curl -s --max-time 5 "$API_URL/conversations/$CONV_ID/result" 2>/dev/null)

            if [ -z "$RESULT" ] || ! echo "$RESULT" | jq -e . >/dev/null 2>&1; then
                echo -e "   ${C_GREEN}✅ Status: COMPLETED${C_RESET}"
                echo -e "   ${C_RED}⚠️  Resultado indisponível ou inválido${C_RESET}"
                echo ""
                continue
            fi

            TITLE=$(echo "$RESULT" | jq -r '.title // "N/A"')
            DESC=$(echo "$RESULT" | jq -r '.description // "N/A"')
            CONF=$(echo "$RESULT" | jq -r '.confidence // 0')
            MSG_COUNT=$(echo "$RESULT" | jq -r '.message_count // 0')
            INTENT_TYPE=$(echo "$RESULT" | jq -r '.intent_type // "other"')
            DEPARTMENTS=$(echo "$RESULT" | jq -r '(.department // []) | join(", ")')
            AGENTS=$(echo "$RESULT" | jq -r '(.agent // []) | join(", ")')

            echo -e "   ${C_GREEN}✅ Status: COMPLETED${C_RESET}"
            echo -e "   📝 Título:     $TITLE"
            echo -e "   📄 Descrição:  $DESC"
            echo -e "   🎯 Confiança:  $CONF"
            echo -e "   🏷️  Intenção:   $INTENT_TYPE"
            echo -e "   🏢 Departamentos: ${DEPARTMENTS:-N/A}"
            echo -e "   👤 Atendentes:   ${AGENTS:-N/A}"
            echo -e "   💬 Mensagens:  $MSG_COUNT"
            ;;
        failed)
            COUNT_FAILED=$((COUNT_FAILED + 1))
            ERROR_MSG=$(echo "$STATUS_JSON" | jq -r '.error // "sem detalhes"' 2>/dev/null)
            echo -e "   ${C_RED}❌ Status: FAILED${C_RESET}"
            echo -e "   ${C_DIM}   Erro: $ERROR_MSG${C_RESET}"
            ;;
        unreachable)
            COUNT_OTHER=$((COUNT_OTHER + 1))
            echo -e "   ${C_RED}🔌 API não respondeu${C_RESET}"
            ;;
        *)
            COUNT_OTHER=$((COUNT_OTHER + 1))
            echo -e "   ${C_YELLOW}⏳ Status: $STATUS_VAL${C_RESET}"
            ;;
    esac
    echo ""
done

echo "========================================"
if [ "$TOTAL" -eq 0 ]; then
    echo -e "${C_YELLOW}📊 Nenhuma conversa encontrada para o filtro '$FILTER'${C_RESET}"
else
    echo -e "${C_BOLD}📊 TOTAL: $TOTAL conversas${C_RESET}  ${C_GREEN}✅ $COUNT_COMPLETED${C_RESET}  ${C_RED}❌ $COUNT_FAILED${C_RESET}  ${C_YELLOW}⏳ $COUNT_OTHER${C_RESET}"
fi
