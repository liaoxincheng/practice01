from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pet_api_client import PetApiClient
from ollama_client import OllamaClient, build_prompt

mcp = MCPServer("pet-hospital", version="1.0.0")

_original_run = mcp.run

def _run(transport="stdio", **kwargs):
    if transport == "streamable-http":
        kwargs.setdefault("stateless_http", True)
    return _original_run(transport, **kwargs)

mcp.run = _run


@mcp.tool()
async def list_pets(
    species: str | None = None,
    gender: str | None = None,
    doctor: str | None = None,
    status: str | None = None,
    search: str | None = None,
    owner_name: str | None = None,
    owner_phone: str | None = None,
    disease: str | None = None,
    min_cost: float | None = None,
    max_cost: float | None = None,
    sort_by: str = "createdAt",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    查询宠物档案列表。支持按种类、性别、医生、状态、疾病等筛选，
    支持全文检索、排序和分页。
    """
    try:
        client = PetApiClient()
        params = {
            "species": species,
            "doctor": doctor,
            "status": status,
            "disease": disease,
            "search": search,
            "owner_name": owner_name,
            "owner_phone": owner_phone,
            "min_cost": min_cost,
            "max_cost": max_cost,
            "sort_by": sort_by,
            "order": order,
            "page": page,
            "page_size": page_size,
        }
        result = await client.list_pets(params)
        if gender:
            result["items"] = [p for p in result.get("items", []) if p.get("gender") == gender]
            result["total"] = len(result["items"])
        await client.close()
        return result
    except Exception as e:
        raise ToolError(f"list_pets 调用失败: {e}")


@mcp.tool()
async def create_pet(
    name: str,
    species: str,
    owner_name: str,
    owner_phone: str,
    disease: str,
    doctor: str,
    gender: str | None = None,
    breed: str | None = None,
    age_months: int | None = None,
    color: str | None = None,
    chip_no: str | None = None,
    owner_addr: str | None = None,
    status: str | None = None,
    allergy: str | None = None,
    note: str | None = None,
) -> dict:
    """
    新增宠物档案。
    必填：name, species, owner_name, owner_phone, disease, doctor
    species 可选值：犬/猫/兔/鸟/仓鼠/爬宠/其他
    gender 可选值：公/母
    status 可选值：待就诊/就诊中/住院中/已康复/慢性病随访
    """
    try:
        client = PetApiClient()
        pet_data = {
            "name": name,
            "species": species,
            "ownerName": owner_name,
            "ownerPhone": owner_phone,
            "disease": disease,
            "doctor": doctor,
        }
        if gender is not None:
            pet_data["gender"] = gender
        if breed is not None:
            pet_data["breed"] = breed
        if age_months is not None:
            pet_data["ageMonths"] = age_months
        if color is not None:
            pet_data["color"] = color
        if chip_no is not None:
            pet_data["chipNo"] = chip_no
        if owner_addr is not None:
            pet_data["ownerAddr"] = owner_addr
        if status is not None:
            pet_data["status"] = status
        if allergy is not None:
            pet_data["allergy"] = allergy
        if note is not None:
            pet_data["note"] = note
        result = await client.create_pet(pet_data)
        await client.close()
        return result
    except Exception as e:
        raise ToolError(f"create_pet 调用失败: {e}")


@mcp.tool()
async def get_pet(pet_id: str) -> dict:
    """
    按档案 ID 查询单只宠物的完整档案，包含全部历史病历 records
    和消费明细 charges。pet_id 形如 PET-000001。
    """
    try:
        client = PetApiClient()
        result = await client.get_pet(pet_id)
        await client.close()
        return result
    except Exception as e:
        raise ToolError(f"get_pet 调用失败: {e}")


@mcp.tool()
async def add_record(
    pet_id: str,
    diagnosis: str,
    doctor: str | None = None,
    visit_date: str | None = None,
    symptoms: str | None = None,
    treatment: str | None = None,
    prescription: list[str] | None = None,
    weight_kg: float | None = None,
    temperature: float | None = None,
    follow_up: str | None = None,
    charge: float | None = None,
) -> dict:
    """
    为指定宠物追加一条历史病历。diagnosis（诊断）必填，其余可选。
    visit_date 格式 YYYY-MM-DD，不传则用后端默认值。
    返回追加后的完整档案，visitCount 与 totalCost 会自动重新汇总。
    """
    try:
        record = {"diagnosis": diagnosis}
        optional = {
            "doctor": doctor,
            "visitDate": visit_date,
            "symptoms": symptoms,
            "treatment": treatment,
            "prescription": prescription,
            "weightKg": weight_kg,
            "temperature": temperature,
            "followUp": follow_up,
            "charge": charge,
        }
        for key, val in optional.items():
            if val is not None:
                record[key] = val
        client = PetApiClient()
        result = await client.add_record(pet_id, record)
        await client.close()
        return result
    except Exception as e:
        raise ToolError(f"add_record 调用失败: {e}")


@mcp.tool()
async def followup_summary(pet_id: str) -> dict:
    """
    为指定宠物生成复诊小结与随访提醒。读取该宠物的全部历史病历，
    交给本机大模型分析，返回病情小结、复查提醒和居家护理建议。
    需要本机 Ollama 服务（默认 http://127.0.0.1:11435）在运行。
    """
    try:
        client = PetApiClient()
        pet = await client.get_pet(pet_id)
        await client.close()

        if not pet.get("records"):
            return {
                "pet_id": pet_id,
                "name": pet.get("name"),
                "summary": "该宠物暂无历史病历，无法生成复诊小结。",
            }

        llm = OllamaClient()
        try:
            text = await llm.generate(build_prompt(pet))
        finally:
            await llm.close()

        return {
            "pet_id": pet_id,
            "name": pet.get("name"),
            "model": llm.model,
            "visitCount": pet.get("visitCount"),
            "summary": text,
        }
    except Exception as e:
        raise ToolError(f"followup_summary 调用失败: {e}")