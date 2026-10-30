"""宠物医院 MCP 工具定义(5 个)。

工具函数只负责:参数校验 → 调客户端 → 把结果或错误交给 MCP 层。
函数签名里的类型提示会被 MCPServer 自动转成 JSON Schema,不需要手写。

工具:
- list_pets         查询档案列表(筛选 / 检索 / 排序 / 分页)
- create_pet        新建档案
- get_pet           按 ID 查看完整档案
- add_record        追加一条病历
- followup_summary  调本机 Ollama 生成复发随访小结
"""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ollama_client import OllamaError, OllamaSummarizer
from pet_api_client import PetApiError, PetHospitalClient

VERSION = "1.0.0"

SPECIES_CHOICES = {"犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"}
STATUS_CHOICES = {"待就诊", "就诊中", "住院中", "已康复", "慢性病随访"}
SORT_FIELDS = {
    "id", "name", "ownerName", "species", "doctor",
    "totalCost", "visitCount", "createdAt", "updatedAt",
}
MAX_PAGE_SIZE = 500

mcp = MCPServer("pet-hospital", version=VERSION)


def _ensure_list_args(
    species: str | None,
    status: str | None,
    sort_by: str,
    order: str,
    page: int,
    page_size: int,
    min_cost: float | None,
    max_cost: float | None,
) -> None:
    """本地校验列表查询参数,错误信息直接给模型/用户看。"""
    if species is not None and species not in SPECIES_CHOICES:
        raise ToolError(f"species 必须是 {'/'.join(sorted(SPECIES_CHOICES))} 之一")
    if status is not None and status not in STATUS_CHOICES:
        raise ToolError(f"status 必须是 {'/'.join(sorted(STATUS_CHOICES))} 之一")
    if sort_by not in SORT_FIELDS:
        raise ToolError(f"sort_by 必须是 {'/'.join(sorted(SORT_FIELDS))} 之一")
    if order not in ("asc", "desc"):
        raise ToolError("order 只能是 asc 或 desc")
    if page < 1:
        raise ToolError("page 从 1 开始")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ToolError(f"page_size 必须在 1 到 {MAX_PAGE_SIZE} 之间")
    if min_cost is not None and max_cost is not None and min_cost > max_cost:
        raise ToolError("min_cost 不能大于 max_cost")


@mcp.tool()
async def list_pets(
    species: str | None = None,
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
) -> dict[str, Any]:
    """查询宠物档案列表。支持按种类、医生、就诊状态、疾病筛选,
    也支持主人姓名/电话、总花费区间、全文检索(search)、排序和分页。

    返回:items(本页档案)、total、page、pageSize、totalPages、totalCost。
    """
    _ensure_list_args(species, status, sort_by, order, page, page_size, min_cost, max_cost)

    client = PetHospitalClient()
    try:
        return await client.list_pets(
            {
                "species": species,
                "doctor": doctor,
                "status": status,
                "search": search,
                "owner_name": owner_name,
                "owner_phone": owner_phone,
                "disease": disease,
                "min_cost": min_cost,
                "max_cost": max_cost,
                "sort_by": sort_by,
                "order": order,
                "page": page,
                "page_size": page_size,
            }
        )
    except PetApiError as exc:
        raise ToolError(f"查询宠物列表失败:{exc}")
    finally:
        await client.aclose()


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
) -> dict[str, Any]:
    """新建一只宠物的档案。必填:name、species、owner_name、owner_phone、
    disease、doctor;其余可选。成功返回带自动生成 id 的完整档案。
    """
    if species not in SPECIES_CHOICES:
        raise ToolError(f"species 必须是 {'/'.join(sorted(SPECIES_CHOICES))} 之一")
    if status is not None and status not in STATUS_CHOICES:
        raise ToolError(f"status 必须是 {'/'.join(sorted(STATUS_CHOICES))} 之一")
    if age_months is not None and age_months < 0:
        raise ToolError("age_months 不能是负数")

    payload: dict[str, Any] = {
        "name": name,
        "species": species,
        "ownerName": owner_name,
        "ownerPhone": owner_phone,
        "disease": disease,
        "doctor": doctor,
    }
    optional = {
        "gender": gender,
        "breed": breed,
        "ageMonths": age_months,
        "color": color,
        "chipNo": chip_no,
        "ownerAddr": owner_addr,
        "status": status,
        "allergy": allergy,
        "note": note,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})

    client = PetHospitalClient()
    try:
        return await client.create_pet(payload)
    except PetApiError as exc:
        raise ToolError(f"新建宠物档案失败:{exc}")
    finally:
        await client.aclose()


@mcp.tool()
async def get_pet(pet_id: str) -> dict[str, Any]:
    """按档案 ID(如 PET-000001)查询完整资料,包括全部历史病历 records
    和消费明细 charges、自动汇总的 visitCount / totalCost。
    """
    client = PetHospitalClient()
    try:
        return await client.get_pet(pet_id)
    except PetApiError as exc:
        raise ToolError(f"查询宠物档案失败:{exc}")
    finally:
        await client.aclose()


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
) -> dict[str, Any]:
    """给指定宠物追加一条历史病历。diagnosis(诊断)必填,其余可选。

    visit_date 格式 YYYY-MM-DD,不传由后端填当天;follow_up 写复查建议原文。
    返回追加后的完整档案,visitCount 与 totalCost 已重新汇总。
    """
    if not (diagnosis or "").strip():
        raise ToolError("diagnosis(诊断)不能为空")
    if weight_kg is not None and weight_kg <= 0:
        raise ToolError("weight_kg 必须大于 0")
    if charge is not None and charge < 0:
        raise ToolError("charge 不能是负数")

    record: dict[str, Any] = {"diagnosis": diagnosis}
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
    record.update({key: value for key, value in optional.items() if value is not None})

    client = PetHospitalClient()
    try:
        return await client.add_record(pet_id, record)
    except PetApiError as exc:
        raise ToolError(f"追加病历失败:{exc}")
    finally:
        await client.aclose()


@mcp.tool()
async def followup_summary(pet_id: str) -> dict[str, Any]:
    """读取一只宠物的全部历史病历,交给本机 Ollama 生成三段式随访小结:
    病情小结、复查提醒、居家护理建议。需要本机 Ollama 正在运行
    (默认 http://127.0.0.1:11435,可用 OLLAMA_URL / OLLAMA_MODEL 修改)。
    """
    api = PetHospitalClient()
    try:
        pet = await api.get_pet(pet_id)
    except PetApiError as exc:
        raise ToolError(f"读取宠物档案失败:{exc}")
    finally:
        await api.aclose()

    if not pet.get("records"):
        return {
            "pet_id": pet.get("id", pet_id),
            "name": pet.get("name"),
            "model": None,
            "summary": "该宠物还没有历史病历,暂时无法生成随访小结。",
        }

    summarizer = OllamaSummarizer()
    try:
        summary = await summarizer.summarize(pet)
    except OllamaError as exc:
        raise ToolError(f"生成随访小结失败:{exc}")
    finally:
        await summarizer.aclose()

    return {
        "pet_id": pet.get("id", pet_id),
        "name": pet.get("name"),
        "model": summarizer.model,
        "visitCount": pet.get("visitCount"),
        "summary": summary,
    }
