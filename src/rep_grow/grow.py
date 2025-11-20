from .repertoire import Repertoire
import asyncio


def main():
    rep = Repertoire.from_str("white", "e4 e5 Nf3 Nc6 Nc3")
    rep.play_initial_moves()
    print("FEN:", rep.fen)
    print("Is player turn?", rep.is_player_turn)
    print("PGN:")
    print(rep.pgn)

    async def expand_all(iteration: int):
        print(
            f"Iteration {iteration}: Adding engine variations to {len(rep.leaf_nodes)} leaf nodes..."
        )
        await rep.add_engine_variations()

    # Loop over the leaf nodes and add engine variations for each (in parallel)
    for i in range(3):
        asyncio.run(expand_all(i + 1))
    print("\nFinal PGN with all engine variations:")
    print(rep.pgn)


if __name__ == "__main__":
    main()
