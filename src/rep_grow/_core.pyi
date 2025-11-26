from typing import Any, Dict, List, Sequence, Tuple

def player_move_analysis(
    nodes: Sequence[Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]: ...
def player_turn_mask(
    side_is_white: bool,
    fens: Sequence[str],
) -> List[bool]: ...
def split_repertoire_nodes(
    root_fen: str,
    nodes: Sequence[Any],
    max_moves: int,
) -> List[Tuple[str, List[str], int]]: ...
def canonicalize_fen(fen: str) -> str: ...
def stockfish_evaluate(
    fen: str,
    engine_path: str,
    depth: int,
    multi_pv: int,
    think_time: float | None,
    pool_size: int,
) -> Dict[str, Any]: ...
