import json
import heapq
from pathlib import Path
from typing import Any


# ============================================================
# 基本設定
# ============================================================

INPUT_FILE = Path(r"C:\Users\User\Desktop\蜂迴路轉_人群的程式碼和各種檔案\程式碼&樣本&結果\Taipei New Year's Eve_neighbors.json")
OUTPUT_FILE = Path(r"C:\Users\User\Desktop\蜂迴路轉_人群的程式碼和各種檔案\程式碼&樣本&結果\Taipei New Year's Eve_map_neww.json")

# 抵達這些出口後，不可以再以出口作為中繼點繼續移動(依地圖更改)
EXIT_IDS = {"E1", "E2", "E3", "E4", "E5", "E6"}

# 只保留 total_cost 為 1、2、3 的疏散區域
MAX_TOTAL_COST = 3


# ============================================================
# 讀取與驗證 JSON
# ============================================================

def load_neighbors_json(file_path: Path) -> list[dict[str, Any]]:
    """讀取 neighbors.json。"""
    if not file_path.exists():
        raise FileNotFoundError(
            f"找不到輸入檔案：{file_path.resolve()}\n"
            "請確認 neighbors.json 與此 Python 程式放在同一個資料夾。"
        )

    try:
        with file_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"neighbors.json 格式錯誤：第 {error.lineno} 行，"
            f"第 {error.colno} 個字元附近。\n"
            f"詳細原因：{error.msg}"
        ) from error

    if not isinstance(data, list):
        raise ValueError("neighbors.json 最外層必須是 JSON 陣列 list。")

    return data


def validate_and_build_graph(
    data: list[dict[str, Any]]
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, int]
]:
    """
    驗證節點資料並建立：
    1. node_data：各節點完整資料
    2. graph：鄰接關係
    3. node_order：節點在原始 JSON 中的順序
    """
    node_data: dict[str, dict[str, Any]] = {}
    graph: dict[str, list[dict[str, Any]]] = {}
    node_order: dict[str, int] = {}

    # 第一輪：建立節點
    for index, node in enumerate(data):
        if not isinstance(node, dict):
            raise ValueError(f"第 {index + 1} 筆節點資料不是 JSON 物件。")

        node_id = str(node.get("id", "")).strip()

        if not node_id:
            raise ValueError(f"第 {index + 1} 筆資料缺少 id。")

        if node_id in node_data:
            raise ValueError(f"發現重複的節點 id：{node_id}")

        max_occupancy = node.get("max_occupancy")
        traversal_cost = node.get("traversal_cost", 0)
        neighbors = node.get("neighbors", [])

        if not isinstance(max_occupancy, (int, float)):
            raise ValueError(
                f"節點 {node_id} 的 max_occupancy 必須是數字。"
            )

        if not isinstance(traversal_cost, int) or traversal_cost < 0:
            raise ValueError(
                f"節點 {node_id} 的 traversal_cost "
                "必須是大於或等於 0 的整數。"
            )

        if not isinstance(neighbors, list):
            raise ValueError(
                f"節點 {node_id} 的 neighbors 必須是陣列。"
            )

        node_data[node_id] = {
            "id": node_id,
            "max_occupancy": max_occupancy,
            "traversal_cost": traversal_cost,
        }

        graph[node_id] = []
        node_order[node_id] = index

    # 第二輪：驗證鄰接關係
    for node in data:
        node_id = str(node["id"]).strip()
        seen_neighbors: set[str] = set()

        for neighbor_index, neighbor in enumerate(node.get("neighbors", [])):
            if not isinstance(neighbor, dict):
                raise ValueError(
                    f"節點 {node_id} 的第 {neighbor_index + 1} 個 "
                    "neighbor 不是 JSON 物件。"
                )

            neighbor_id = str(neighbor.get("id", "")).strip()
            edge_cost = neighbor.get("cost")

            if not neighbor_id:
                raise ValueError(
                    f"節點 {node_id} 的第 {neighbor_index + 1} 個 "
                    "neighbor 缺少 id。"
                )

            if neighbor_id not in node_data:
                raise ValueError(
                    f"節點 {node_id} 連到不存在的節點：{neighbor_id}"
                )

            if neighbor_id == node_id:
                raise ValueError(
                    f"節點 {node_id} 不應連到自己。"
                )

            if neighbor_id in seen_neighbors:
                raise ValueError(
                    f"節點 {node_id} 的 neighbors 中重複出現 "
                    f"{neighbor_id}。"
                )

            if not isinstance(edge_cost, int) or edge_cost <= 0:
                raise ValueError(
                    f"節點 {node_id} → {neighbor_id} 的 cost "
                    "必須是大於 0 的整數。"
                )

            seen_neighbors.add(neighbor_id)

            graph[node_id].append({
                "id": neighbor_id,
                "cost": edge_cost,
            })

    return node_data, graph, node_order


# ============================================================
# 最短成本計算
# ============================================================

def calculate_nearby_zones(
    start_id: str,
    node_data: dict[str, dict[str, Any]],
    graph: dict[str, list[dict[str, Any]]],
    node_order: dict[str, int],
) -> list[dict[str, Any]]:
    """
    使用 Dijkstra 計算從 start_id 出發的最小 total_cost。

    成本規則：
    - 從起點直接移動時，只加 edge cost。
    - 從中繼節點繼續移動時，加：
        中繼節點 traversal_cost + edge cost
    - 抵達 E1～E4 後停止展開。
    - 只保留 total_cost = 1、2、3。
    """

    # 若起點本身就是出口，視為已完成疏散，不再往外搜尋
    if start_id in EXIT_IDS:
        return []

    infinity = float("inf")

    shortest_cost: dict[str, float] = {
        node_id: infinity for node_id in node_data
    }
    shortest_cost[start_id] = 0

    # priority queue 格式：(目前累積成本, 節點原始順序, 節點 id)
    priority_queue: list[tuple[int, int, str]] = [
        (0, node_order[start_id], start_id)
    ]

    while priority_queue:
        current_cost, _, current_id = heapq.heappop(priority_queue)

        # 已經找到更短路線，略過舊資料
        if current_cost != shortest_cost[current_id]:
            continue

        # 超過成本 3，不需要再往下搜尋
        if current_cost > MAX_TOTAL_COST:
            continue

        # # 抵達出口後，不可以再經由出口繼續移動(依地圖更改)
        if current_id in EXIT_IDS:
            continue

        # 起點不算 traversal_cost；
        # 只有把某節點當成中繼點、再從它離開時才計算。
        if current_id == start_id:
            traversal_cost = 0
        else:
            traversal_cost = node_data[current_id]["traversal_cost"]

        for edge in graph[current_id]:
            next_id = edge["id"]
            edge_cost = edge["cost"]

            new_total_cost = (
                current_cost
                + traversal_cost
                + edge_cost
            )

            # 其他成本不列入疏散範圍，也不需要繼續搜尋
            if new_total_cost > MAX_TOTAL_COST:
                continue

            if new_total_cost < shortest_cost[next_id]:
                shortest_cost[next_id] = new_total_cost

                heapq.heappush(
                    priority_queue,
                    (
                        new_total_cost,
                        node_order[next_id],
                        next_id,
                    ),
                )

    nearby_zone: list[dict[str, Any]] = []

    for destination_id, total_cost in shortest_cost.items():
        if destination_id == start_id:
            continue

        if total_cost in {1, 2, 3}:
            nearby_zone.append({
                "id": destination_id,
                "hops": int(total_cost),
            })

    # 先依 hops 排序，同一 hops 再依原始 JSON 順序排列
    nearby_zone.sort(
        key=lambda item: (
            item["hops"],
            node_order[item["id"]],
        )
    )

    return nearby_zone


# ============================================================
# 產生輸出 JSON
# ============================================================

def generate_output(
    node_data: dict[str, dict[str, Any]],
    graph: dict[str, list[dict[str, Any]]],
    node_order: dict[str, int],
) -> list[dict[str, Any]]:
    """為每個節點產生 nearby_zone。"""
    output_data: list[dict[str, Any]] = []

    ordered_node_ids = sorted(
        node_data.keys(),
        key=lambda node_id: node_order[node_id],
    )

    for node_id in ordered_node_ids:
        nearby_zone = calculate_nearby_zones(
            start_id=node_id,
            node_data=node_data,
            graph=graph,
            node_order=node_order,
        )

        output_data.append({
            "id": node_id,
            "max_occupancy": node_data[node_id]["max_occupancy"],
            "nearby_zone": nearby_zone,
        })

    return output_data


def save_output_json(
    output_data: list[dict[str, Any]],
    file_path: Path,
) -> None:
    """儲存輸出 JSON。"""
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(
            output_data,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# 主程式
# ============================================================

def main() -> None:
    try:
        input_data = load_neighbors_json(INPUT_FILE)

        node_data, graph, node_order = validate_and_build_graph(
            input_data
        )

        output_data = generate_output(
            node_data=node_data,
            graph=graph,
            node_order=node_order,
        )

        save_output_json(
            output_data=output_data,
            file_path=OUTPUT_FILE,
        )

        print("轉換完成！")
        print(f"輸入檔案：{INPUT_FILE.resolve()}")
        print(f"輸出檔案：{OUTPUT_FILE.resolve()}")
        print(f"節點數量：{len(output_data)}")

    except (FileNotFoundError, ValueError) as error:
        print("\n程式執行失敗：")
        print(error)

    except Exception as error:
        print("\n發生未預期的錯誤：")
        print(f"{type(error).__name__}: {error}")


if __name__ == "__main__":
    main()