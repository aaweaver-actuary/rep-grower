import asyncio
import chess

from .repertoire import Repertoire


def main():
    rep = Repertoire.from_str("white", "e4 e5 Nf3 Nc6 Nc3")
    rep.play_initial_moves()
    print("FEN:", rep.fen)
    print("Is player turn?", rep.is_player_turn)
    print("PGN:")
    print(rep.pgn)

    async def expand_by_turn(iteration: int):
        player_nodes = 0
        opponent_nodes = 0
        for node in rep.leaf_nodes:
            board = chess.Board(node.fen)
            if board.turn == rep.side:
                player_nodes += 1
            else:
                opponent_nodes += 1
        print(
            "Iteration {iter}: expanding {player} player-turn and {opp} opponent-turn leaf nodes...".format(
                iter=iteration, player=player_nodes, opp=opponent_nodes
            )
        )
        await rep.expand_leaves_by_turn()

    for i in range(10):
        asyncio.run(expand_by_turn(i + 1))

    print("\nFinal PGN with all engine variations:")
    print(rep.pgn)


if __name__ == "__main__":
    main()
