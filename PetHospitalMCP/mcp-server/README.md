# Pet Hospital MCP Server

MCP 2026-07-28 Python server. 5 tools over the pet hospital REST API.

## Dependencies

- Go backend `pethospital.exe` running on `http://127.0.0.1:8080`
- For `followup_summary` only: local Ollama on `http://127.0.0.1:11435`
  (bundled one: `D:\Anythingllm\resources\ollama\llm.exe serve`, model `qwen3-vl:4b-instruct`)

## Tools

### list_pets
Query pets with filters: species, gender, doctor, status, disease, search, owner_name, owner_phone, min_cost, max_cost, sort_by, order, page, page_size.

### create_pet
Create new pet record. Required: name, species, owner_name, owner_phone, disease, doctor.
Optional: gender, breed, age_months, color, chip_no, owner_addr, status, allergy, note.

### get_pet
Full profile of one pet by id (e.g. `PET-000001`), including `records` and `charges`.

### add_record
Append one medical record. Required: pet_id, diagnosis.
Optional: doctor, visit_date, symptoms, treatment, prescription, weight_kg, temperature, follow_up, charge.
Note: `charge` on a record is informational only — it does not change `totalCost`.

### followup_summary
Ask the local model for a follow-up summary of one pet's whole record history.

## Start

stdio (what Qoder uses): `MCP_TRANSPORT=stdio python server.py`
HTTP: `python server.py` (port 8081)

## Test

`python test_mcp.py` — spawns the server over stdio, exercises all 5 tools against a
throwaway pet, deletes it, writes `test_mcp_result.json`. Needs the Go backend running.
