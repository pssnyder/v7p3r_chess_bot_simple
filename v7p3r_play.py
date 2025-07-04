# v7p3r_engine/play_v7p3r.py

import os
import sys
import pygame
import chess
import chess.pgn
import datetime
import logging
import socket
from typing import Optional
import time # Import time for measuring move duration
from io import StringIO
import hashlib
from v7p3r_config import v7p3rConfig

CONFIG_NAME = "custom_config"

# Define the maximum frames per second for the game loop
MAX_FPS = 60

# Set engine availability values
RL_ENGINE_AVAILABLE = False
GA_ENGINE_AVAILABLE = False
NN_ENGINE_AVAILABLE = False

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    base = getattr(sys, '_MEIPASS', None)
    if base:
        return os.path.join(base, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# =====================================
# ========== LOGGING SETUP ============
def get_timestamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# Create logging directory relative to project root
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
log_dir = os.path.join(project_root, 'logging')
if not os.path.exists(log_dir):
    os.makedirs(log_dir, exist_ok=True)

# Setup individual logger for this file
timestamp = get_timestamp()
log_filename = f"v7p3r_play_{timestamp}.log"
log_file_path = os.path.join(log_dir, log_filename)

v7p3r_play_logger = logging.getLogger(f"v7p3r_play_{timestamp}")
v7p3r_play_logger.setLevel(logging.DEBUG)

if not v7p3r_play_logger.handlers:
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=10*1024*1024,
        backupCount=3,
        delay=True
    )
    formatter = logging.Formatter(
        '%(asctime)s | %(funcName)-15s | %(message)s',
        datefmt='%H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    v7p3r_play_logger.addHandler(file_handler)
    v7p3r_play_logger.propagate = False

# Import necessary modules from v7p3r_engine
from v7p3r import v7p3rEngine # Corrected import for v7p3rEngine
from v7p3r_stockfish_handler import StockfishHandler

class v7p3rChess:
    def __init__(self, config_name: Optional[str] = None):
        """
        config: ChessGameConfig object containing all configuration parameters.
        """
        # Initialize Pygame (even in headless mode, for internal timing)
        pygame.init()
        self.clock = pygame.time.Clock()

        # Enable logging
        self.logger = v7p3r_play_logger

        # Load configuration
        try:
            if config_name is None:
                self.config_manager = v7p3rConfig()
                self.config = self.config_manager.get_config()
                self.game_config = self.config_manager.get_game_config()
                self.engine_config = self.config_manager.get_engine_config()
                self.stockfish_config = self.config_manager.get_stockfish_config()
                self.puzzle_config = self.config_manager.get_puzzle_config()
                if self.logger:
                    self.logger.info("No configuration provided, using default v7p3r configuration.")
            else:
                self.config_name = config_name
                self.config_manager = v7p3rConfig(config_path=os.path.join('configs',f"{self.config_name}.json"))
                self.game_config = self.config_manager.get_game_config()
                self.engine_config = self.config_manager.get_engine_config()
                self.stockfish_config = self.config_manager.get_stockfish_config()
                if self.logger:
                    self.logger.info("Configuration provided, using custom settings.")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error loading configuration: {e}")
            raise

        # Initialize Engines
        self.engine = v7p3rEngine()
        
        # Debug stockfish config before passing to handler
        if self.logger:
            self.logger.info(f"Stockfish config being passed to handler: {self.stockfish_config}")
            self.logger.info(f"Stockfish path in config: {self.stockfish_config.get('stockfish_path', 'NOT_FOUND')}")
        
        try:
            self.stockfish = StockfishHandler(stockfish_config=self.stockfish_config)
            if self.logger:
                self.logger.info("StockfishHandler initialized successfully")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to initialize StockfishHandler: {e}")
            print(f"Error initializing Stockfish: {e}")
            # Create a dummy stockfish handler that always returns null moves
            class DummyStockfish:
                def __init__(self):
                    self.process = None
                    self.nodes_searched = 0
                    self.last_search_info = {}
                
                def search(self, board, player, config):
                    print("DummyStockfish: returning null move due to initialization failure")
                    return chess.Move.null()
                
                def cleanup(self):
                    pass
                    
                def quit(self):
                    pass
                    
                def get_last_search_info(self):
                    return {}
            
            self.stockfish = DummyStockfish()
        
        # Check if stockfish process is running
        if hasattr(self.stockfish, 'process') and self.stockfish.process is None:
            print("WARNING: Stockfish process is None - engine failed to start!")
        
        # Set logging level
        self.monitoring_enabled = self.engine_config.get("monitoring_enabled", True)
        self.verbose_output_enabled = self.engine_config.get("verbose_output", True)

        # Initialize new engines based on availability and configuration
        self.engines = {
            'v7p3r': self.engine,
            'stockfish': self.stockfish
        }
        
        # Game Settings
        self.game_count = self.game_config.get("game_count", 1)
        self.white_player = self.game_config.get("white_player", "v7p3r")
        self.black_player = self.game_config.get("black_player", "stockfish")
        self.starting_position = self.game_config.get("starting_position", "default")
        
        # Prepare engine configurations for metrics
        self.white_engine_config = self._get_engine_config_for_player(self.white_player)
        self.black_engine_config = self._get_engine_config_for_player(self.black_player)

        # Access global engine availability variables
        global RL_ENGINE_AVAILABLE, GA_ENGINE_AVAILABLE, NN_ENGINE_AVAILABLE

        if 'v7p3r_rl' in self.engines:
            # Import new engines
            try:
                from v7p3r_rl import v7p3rRLEngine
                RL_ENGINE_AVAILABLE = True
            except ImportError:
                RL_ENGINE_AVAILABLE = False
                print("Warning: v7p3r_rl_engine not available")
        
        if 'v7p3r_ga' in self.engines:
            try:
                from v7p3r_ga import v7p3rGeneticAlgorithm
                GA_ENGINE_AVAILABLE = True
            except ImportError:
                GA_ENGINE_AVAILABLE = False
                print("Warning: v7p3r_ga_engine not available")

        if 'v7p3r_nn' in self.engines:
            try:
                from v7p3r_nn import v7p3rNeuralNetwork
                NN_ENGINE_AVAILABLE = True
            except ImportError:
                NN_ENGINE_AVAILABLE = False
                print("Warning: v7p3r_nn_engine not available")

        # Initialize RL engine if available
        if RL_ENGINE_AVAILABLE and 'v7p3r_rl' in self.engines:
            try:
                # Use centralized config instead of separate config file
                self.rl_engine = v7p3rRLEngine(self.config_manager)
                self.engines['v7p3r_rl'] = self.rl_engine
                print("✓ v7p3r RL engine initialized")
            except Exception as e:
                print(f"Warning: Failed to initialize RL engine: {e}")
        
        # Initialize GA engine if available
        if GA_ENGINE_AVAILABLE and 'v7p3r_ga' in self.engines:
            try:
                from v7p3r_ga import v7p3rGeneticAlgorithm
                # Use centralized config instead of separate config file
                self.ga_engine = v7p3rGeneticAlgorithm(self.config_manager)
                self.engines['v7p3r_ga'] = self.ga_engine
                print("✓ v7p3r GA engine initialized for gameplay")
            except Exception as e:
                print(f"Warning: Failed to initialize GA engine: {e}")
        
        # Initialize NN engine if available
        if NN_ENGINE_AVAILABLE and 'v7p3r_nn' in self.engines:
            try:
                # Use centralized config instead of separate config file
                self.nn_engine = self._create_nn_engine_wrapper(self.config_manager)
                if self.nn_engine:
                    self.engines['v7p3r_nn'] = self.nn_engine
                    print("✓ v7p3r NN engine wrapper initialized")
            except Exception as e:
                print(f"Warning: Failed to initialize NN engine: {e}")

        # Initialize board and new game
        self.new_game()
    
    def new_game(self):
        """Reset the game state for a new game"""
        self.board = chess.Board()
        if self.starting_position != "default":
            self.board.set_fen(self.starting_position)
        self.game = chess.pgn.Game()
        self.game_node = self.game
        self.selected_square = chess.SQUARES[0]
        self.last_engine_move = chess.Move.null()
        self.current_eval = 0.0
        self.current_player = self.board.turn
        self.last_move = chess.Move.null()
        self.move_history = []
        self.move_start_time = 0
        self.move_end_time = 0
        self.move_duration = 0
        self.game_start_time = time.time()  # Track overall game timing

        # Reset PGN headers and file
        self.set_headers()
        self.quick_save_pgn("logging/active_game.pgn")
        
        if self.monitoring_enabled and self.logger:
            self.logger.info(f"Starting new game: {self.current_game_db_id}.")

    def set_headers(self):
        self.game.headers["Event"] = "v7p3r Engine Chess Game"
        self.game.headers["White"] = f"{self.white_player}"
        self.game.headers["Black"] = f"{self.black_player}"
        self.game.headers["Date"] = datetime.datetime.now().strftime("%Y.%m.%d")
        self.game.headers["Site"] = socket.gethostbyname(socket.gethostname())
        self.game.headers["Round"] = "#"

    def get_board_result(self):
        """Return the result string for the current board state."""
        # Explicitly handle all draw and win/loss cases, fallback to "*"
        if self.board.is_checkmate():
            # The side to move is checkmated, so the other side wins
            return "1-0" if self.board.turn == chess.BLACK else "0-1"
        # Explicit draw conditions
        if (
            self.board.is_stalemate()
            or self.board.is_insufficient_material()
            or self.board.can_claim_fifty_moves()
            or self.board.can_claim_threefold_repetition()
            or self.board.is_seventyfive_moves()
            or self.board.is_fivefold_repetition()
            or self.board.is_variant_draw()
        ):
            return "1/2-1/2"
        # If the game is over but not by checkmate or above draws, fallback to chess.Board.result()
        if self.board.is_game_over():
            result = self.board.result()
            # Defensive: If result is not a valid string, force draw string
            if result not in ("1-0", "0-1", "1/2-1/2"):
                return "1/2-1/2"
            return result
        return "*"

    def handle_game_end(self):
        """Check if the game is over and handle end conditions."""
        if self.board.is_game_over():
            # Ensure the result is set in the PGN headers and game node
            result = self.get_board_result()
            self.game.headers["Result"] = result
            self.game_node = self.game.end()
            self.save_game_data()
            print(f"\nGame over: {result}")
            return True
        return False
    
    def record_evaluation(self):
        """Record evaluation score in PGN comments"""
        score = self.engine.scoring_calculator.evaluate_position(self.board)
        self.current_eval = score
        self.game_node.comment = f"Eval: {score:.2f}"
            
    def save_game_data(self):
        """Save the game data to local files and database only."""
        games_dir = "games"
        os.makedirs(games_dir, exist_ok=True)
        timestamp = self.game_start_timestamp
        game_id = f"eval_game_{timestamp}"

        # Finalize game and headers
        result = self.get_board_result()
        self.game.headers["Result"] = result
        self.game_node = self.game.end()

        # --- Get PGN text ---
        buf = StringIO()
        exporter = chess.pgn.FileExporter(buf)
        self.game.accept(exporter)
        pgn_text = buf.getvalue()
        
        # Save PGN data to metrics
        # TODO Add code

        # Save locally into pgn file
        pgn_filepath = f"games/eval_game_{timestamp}.pgn"
        with open(pgn_filepath, "w") as f:
            exporter = chess.pgn.FileExporter(f)
            self.game.accept(exporter)
            # Always append the result string at the end for compatibility
            if result != "*":
                f.write(f"\n{result}\n")
        if self.monitoring_enabled and self.logger:
            self.logger.info(f"Game PGN saved to {pgn_filepath}")

        # Save JSON config file for metrics processing
        config_filepath = f"games/eval_game_{timestamp}.json"
        
        # Save a combined config for this specific game, including relevant parts of all loaded configs
        game_specific_config = {
            "game_settings": self.game_config,
            "engine_settings": self.engine.engine_config,
            "stockfish_settings": self.stockfish_config,
            "white_player": self.white_player,
            "black_player": self.black_player,
            "game_id": game_id,
            "ruleset": self.config_manager.ruleset
        }
        with open(config_filepath, "w") as f:
            import json
            json.dump(game_specific_config, f, indent=4)
        if self.monitoring_enabled and self.logger:
            self.logger.info(f"Game-specific combined configuration saved to {config_filepath}")

        # Save the game result to a file for instant analysis
        self.quick_save_pgn(f"games/{game_id}.pgn")

    def quick_save_pgn_to_file(self, filename):
        """Quick save the current game to a PGN file"""
        # Inject or update Result header so the PGN shows the game outcome
        if self.board.is_game_over():
            self.game.headers["Result"] = self.get_board_result()
            self.game_node = self.game.end()
        else:
            self.game.headers["Result"] = "*"
        
        with open(filename, "w") as f:
            exporter = chess.pgn.FileExporter(f)
            self.game.accept(exporter)

    def quick_save_pgn(self, filename):
        """Save PGN to local file."""
        try:
            # Ensure games directory exists
            import os
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            
            with open(filename, 'w', encoding='utf-8') as f:
                # Get PGN text
                buf = StringIO()
                exporter = chess.pgn.FileExporter(buf)
                self.game.accept(exporter)
                f.write(buf.getvalue())
        except Exception as e:
            if self.monitoring_enabled and self.logger:
                self.logger.error(f"[Error] Failed to save PGN to {filename}: {e}")

    def save_local_game_files(self, game_id, pgn_text):
        """Save both PGN and JSON config files locally for metrics processing."""
        try:
            import os
            import json
            
            # Ensure games directory exists
            os.makedirs("games", exist_ok=True)
            
            # Save PGN file
            pgn_path = f"games/{game_id}.pgn"
            with open(pgn_path, 'w', encoding='utf-8') as f:
                f.write(pgn_text)
            
            # Save JSON config file for metrics processing
            json_path = f"games/{game_id}.json"
            config_data = {
                'game_id': game_id,
                'timestamp': self.game_start_timestamp,
                'engine_config': self.engine.engine_config,
            }
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4)
            
            if self.monitoring_enabled and self.logger:
                self.logger.info(f"Saved local files: {pgn_path}, {json_path}")
                
        except Exception as e:
            if self.monitoring_enabled and self.logger:
                self.logger.error(f"[Error] Failed to save local game files for {game_id}: {e}")

    def import_fen(self, fen_string):
        """Import a position from FEN notation"""
        try:
            new_board = chess.Board(fen_string)
            
            if not new_board.is_valid():
                if self.monitoring_enabled and self.logger:
                    self.logger.error(f"[Error] Invalid FEN position: {fen_string}")
                return False

            self.board = new_board
            self.game = chess.pgn.Game()
            self.game.setup(new_board)
            self.game_node = self.game            
            self.selected_square = None
            self.game.headers["FEN"] = fen_string

            if self.monitoring_enabled and self.logger:
                self.logger.info(f"Successfully imported FEN: {fen_string}")
            return True

        except Exception as e:
            if self.monitoring_enabled and self.logger:
                self.logger.error(f"[Error] Unexpected problem importing FEN: {e}")
            return False

    # ===================================
    # ========= MOVE HANDLERS ===========
    
    def process_engine_move(self):
        """Process move for the engine"""
        engine_move = chess.Move.null()
        self.current_eval = 0.0
        self.current_player = self.board.turn
        self.move_start_time = time.time()  # Start timing the move
        self.move_end_time = 0
        self.move_duration = 0
        if self.monitoring_enabled and self.logger:
            self.logger.info(f"Processing move for {self.white_player if self.current_player == chess.WHITE else self.black_player} using {self.engine.name} engine.")
        print(f"{self.white_player if self.current_player == chess.WHITE else self.black_player} is thinking...")

        # Send the move request to the appropriate engine or human interface
        if (self.white_player.lower() == 'human' and self.current_player == chess.WHITE) or (self.black_player.lower() == 'human' and self.current_player == chess.BLACK):
            # Handle human player input (not implemented here, placeholder)
            if self.monitoring_enabled and self.logger:
                self.logger.info("Waiting for human player input...")
            return
        else:
            # Determine current player engine
            current_engine_name = self.white_player.lower() if self.current_player == chess.WHITE else self.black_player.lower()

            # Handle different engine types
            if current_engine_name == 'v7p3r':
                try:
                    # Debug logging to track the issue (always log this)
                    if self.monitoring_enabled and self.logger:
                        self.logger.info(f"Engine type: {type(self.engine)}")
                        self.logger.info(f"Search engine type: {type(self.engine.search_engine)}")
                        self.logger.info(f"Search method type: {type(self.engine.search_engine.search)}")
                        self.logger.info(f"Search method is callable: {callable(getattr(self.engine.search_engine, 'search', None))}")
                        if hasattr(self.engine.search_engine, 'search'):
                            search_attr = getattr(self.engine.search_engine, 'search')
                            if isinstance(search_attr, dict):
                                self.logger.error(f"FOUND THE ISSUE! search attribute is a dict: {search_attr}")
                            self.logger.info(f"Search attribute: {search_attr}")
                    
                    # Use the v7p3r engine for the current player
                    engine_move = self.engine.search_engine.search(self.board, self.current_player)
                except Exception as e:
                    if self.monitoring_enabled and self.logger:
                        self.logger.error(f"[HARDSTOP Error] Cannot find move via v7p3rSearch: {e}. | FEN: {self.board.fen()}")
                        self.logger.error(f"Engine type: {type(self.engine)}")
                        self.logger.error(f"Search engine type: {type(self.engine.search_engine)}")
                        self.logger.error(f"Search engine search attr: {type(getattr(self.engine.search_engine, 'search', 'NOT_FOUND'))}")
                    print(f"HARDSTOP ERROR: Cannot find move via v7p3rSearch: {e}. | FEN: {self.board.fen()}")
                    return
                    
            elif current_engine_name == 'stockfish':
                try:
                    # Debug: Check if Stockfish process is running
                    if self.stockfish.process is None:
                        print("DEBUG: Stockfish process is None! Cannot search.")
                        return
                    # Use the Stockfish engine for the current player
                    if self.monitoring_enabled and self.logger:
                        self.logger.info("Using Stockfish engine for move processing.")
                    engine_move = self.stockfish.search(self.board, chess.WHITE, self.stockfish_config)
                except Exception as e:
                    if self.monitoring_enabled and self.logger:
                        self.logger.error(f"[HARDSTOP Error] Cannot find move via Stockfish: {e}. | FEN: {self.board.fen()}")
                    print(f"HARDSTOP ERROR: Cannot find move via Stockfish: {e}. | FEN: {self.board.fen()}")
                    return
            
            elif current_engine_name == 'v7p3r_rl' and 'v7p3r_rl' in self.engines:
                try:
                    # Use the RL engine for the current player
                    if self.monitoring_enabled and self.logger:
                        self.logger.info("Using v7p3r RL engine for move processing.")
                    engine_move = self.engines['v7p3r_rl'].search(self.board, self.current_player)
                except Exception as e:
                    if self.monitoring_enabled and self.logger:
                        self.logger.error(f"[HARDSTOP Error] Cannot find move via v7p3rReinforcementLearning: {e}. | FEN: {self.board.fen()}")
                    print(f"HARDSTOP ERROR: Cannot find move via v7p3rReinforcementLearning: {e}. | FEN: {self.board.fen()}")
                    return
                    
            elif current_engine_name == 'v7p3r_ga' and 'v7p3r_ga' in self.engines:
                try:
                    # Use the GA engine
                    if self.monitoring_enabled and self.logger:
                        self.logger.info("Using v7p3r GA engine for move processing.")
                    engine_move = self.engines['v7p3r_ga'].search(self.board, self.current_player)
                except Exception as e:
                    if self.monitoring_enabled and self.logger:
                        self.logger.error(f"[HARDSTOP Error] Cannot find move via v7p3rGeneticAlgorithm: {e}. | FEN: {self.board.fen()}")
                    print(f"HARDSTOP ERROR: Cannot find move via v7p3r_ga: {e}. | FEN: {self.board.fen()}")
                    return
                    
            elif current_engine_name == 'v7p3r_nn' and 'v7p3r_nn' in self.engines:
                try:
                    # Use the NN engine
                    if self.monitoring_enabled and self.logger:
                        self.logger.info("Using v7p3r NN engine for move processing.")
                    engine_move = self.engines['v7p3r_nn'].search(self.board, self.current_player)
                except Exception as e:
                    if self.monitoring_enabled and self.logger:
                        self.logger.error(f"[HARDSTOP Error] Cannot find move via v7p3rNeuralNetwork: {e}. | FEN: {self.board.fen()}")
                    print(f"HARDSTOP ERROR: Cannot find move via v7p3rNeuralNetwork: {e}. | FEN: {self.board.fen()}")
                    return
            else:
                if self.monitoring_enabled and self.logger:
                    self.logger.error(f"[HARDSTOP Error] No valid engine in configuration: {current_engine_name}. | FEN: {self.board.fen()}")
                print(f"HARDSTOP ERROR: No valid engine in configuration: {current_engine_name}. | FEN: {self.board.fen()}")
                return

            # Check and Push the move
            if not isinstance(engine_move, chess.Move):
                return # Move invalid
                
            # Initialize variables before try block to ensure they're always defined
            fen_before_move = self.board.fen()
            move_number = self.board.fullmove_number
            
            try:
                if self.board.is_legal(engine_move):
                    self.push_move(engine_move)
                    self.last_engine_move = engine_move
                    self.move_end_time = time.time()  # End timing the move
                    self.move_duration = self.move_end_time - self.move_start_time
                    self.pv_line = ""
                else:
                    if self.monitoring_enabled and self.logger:
                        self.logger.error(f"[HARDSTOP Error] Illegal move: {engine_move} | FEN: {self.board.fen()}")
                    print(f"HARDSTOP ERROR: Illegal move: {engine_move} | FEN: {self.board.fen()}")
                    return
            except Exception as e:
                if self.monitoring_enabled and self.logger:
                    self.logger.error(f"[HARDSTOP Error] Move Invalid: {e}. | Move: {engine_move} | FEN: {self.board.fen()}")
                print(f"HARDSTOP ERROR: Move Invalid: {e}. | Move: {engine_move} | FEN: {self.board.fen()}")
                return

            # Print the move and eval for the watcher, it will now be Black's turn so invert the colors when determining who just played
            print(f"{self.white_player if self.current_player == chess.BLACK else self.black_player} played: {engine_move} after {self.move_duration:.4f}s (Eval: {self.current_eval:.2f})")
            
            # Initialize logged_nodes
            logged_nodes = 0
            
            if self.monitoring_enabled and self.logger:
                # Use the nodes_searched from the appropriate engine
                current_engine_name = self.white_player.lower() if self.current_player == chess.WHITE else self.black_player.lower()
                try:
                    if current_engine_name == 'v7p3r' and hasattr(self.engine.search_engine, 'nodes_searched'):
                        logged_nodes = self.engine.search_engine.nodes_searched
                    else:
                        logged_nodes = 0
                except:
                    logged_nodes = 0
                    
                self.logger.info(f"{self.white_player if self.current_player == chess.WHITE else self.black_player} played: {engine_move} (Eval: {self.current_eval:.2f}) | Time: {self.move_duration:.4f}s | Nodes: {logged_nodes}")

    def push_move(self, move):
        """ Test and push a move to the board and game node """
        if not self.board.is_valid():
            if self.monitoring_enabled and self.logger:
                self.logger.error(f"[Error] Invalid board state detected! | FEN: {self.board.fen()}")
            return False
        
        if self.monitoring_enabled and self.logger:
            self.logger.info(f"Attempting to push move from {'White Engine' if self.board.turn == chess.WHITE else 'Black Engine'}: {move} | FEN: {self.board.fen()}")

        if isinstance(move, str):
            try:
                move = chess.Move.from_uci(move)
                if self.monitoring_enabled and self.logger:
                    self.logger.info(f"Converted to chess.Move move from UCI string before push: {move}")
            except ValueError:
                if self.monitoring_enabled and self.logger:
                    self.logger.error(f"[Error] Invalid UCI string received: {move}")
                return False
        
        if not self.board.is_legal(move):
            if self.monitoring_enabled and self.logger:
                self.logger.info(f"[Error] Illegal move blocked from being pushed: {move}")
            return False
        
        try:
            if self.monitoring_enabled and self.logger:
                self.logger.info(f"No remaining checks, pushing move: {move} | FEN: {self.board.fen()}")
            
            self.board.push(move)
            self.game_node = self.game_node.add_variation(move)
            self.last_move = move
            if self.monitoring_enabled and self.logger:
                self.logger.info(f"Move pushed successfully: {move} | FEN: {self.board.fen()}")
            
            self.current_player = self.board.turn
            
            if self.engine.name.lower() == 'v7p3r':
                self.record_evaluation()
            
            # If the move ends the game, set the result header and end the game node
            if self.board.is_game_over():
                result = self.get_board_result()
                self.game.headers["Result"] = result
                self.game_node = self.game.end()
            else:
                self.game.headers["Result"] = "*"
            
            self.quick_save_pgn("logging/active_game.pgn")
            
            return True
        except ValueError as e:
            if self.monitoring_enabled and self.logger:
                self.logger.error(f"[Error] ValueError pushing move {move}: {e}. Dumping PGN to error_dump.pgn")
            self.quick_save_pgn("games/game_error_dump.pgn")
            return False
            
        except Exception as e:
            if self.monitoring_enabled and self.logger:
                self.logger.error(f"[Error] Exception pushing move {move}: {e}. Dumping PGN to error_dump.pgn")
            self.quick_save_pgn("games/game_error_dump.pgn")
            return False
    
    def _create_nn_engine_wrapper(self, nn_config_path):
        """Create a wrapper for NN engine if it doesn't have proper game interface."""
        try:
            # Import the NN engine class here to avoid undefined reference
            from v7p3r_nn import v7p3rNeuralNetwork
            
            # Try to create NN engine, but it might not have search interface
            nn_engine = v7p3rNeuralNetwork(nn_config_path)
            
            # Check if it has search method
            if hasattr(nn_engine, 'search'):
                return nn_engine
            else:
                # Create a wrapper
                class NNEngineWrapper:
                    def __init__(self, nn_engine):
                        self.nn_engine = nn_engine
                        self.v7p3r_engine = v7p3rEngine()
                    
                    def search(self, board, player_color, engine_config=None):
                        """Search using NN-assisted evaluation."""
                        # Fallback to v7p3r engine for now
                        return self.v7p3r_engine.search_engine.search(board, player_color)
                    
                    def cleanup(self):
                        """Cleanup resources."""
                        if hasattr(self.nn_engine, 'cleanup'):
                            self.nn_engine.cleanup()
                
                return NNEngineWrapper(nn_engine)
                
        except Exception as e:
            print(f"Failed to create NN engine wrapper: {e}")
            return None

    def _get_engine_config_for_player(self, player_name: str) -> dict:
        """
        Get the appropriate engine configuration for a given player
        """
        player_lower = player_name.lower()
        
        if player_lower == 'v7p3r':
            return {
                'name': getattr(self.engine, 'name', 'v7p3r'),
                'version': getattr(self.engine, 'version', 'unknown'),
                'search_algorithm': getattr(self.engine, 'search_algorithm', 'minimax'),
                'depth': getattr(self.engine, 'depth', 2),
                'max_depth': getattr(self.engine, 'max_depth', 2),
                'ruleset': getattr(self.config_manager, 'ruleset', 'standard') if hasattr(self, 'config_manager') else 'standard',
                'use_game_phase': getattr(self.engine, 'use_game_phase', True)
            }
        elif player_lower == 'stockfish':
            return {
                'name': 'stockfish',
                'version': 'unknown',
                'search_algorithm': 'stockfish',
                'depth': self.stockfish_config.get('depth', 2),
                'max_depth': self.stockfish_config.get('max_depth', 2),
                'elo_rating': self.stockfish_config.get('elo_rating', 1000),
                'skill_level': self.stockfish_config.get('skill_level', 5),
                'movetime': self.stockfish_config.get('movetime', 1000)
            }
        elif player_lower in ['v7p3r_rl', 'v7p3r_ga', 'v7p3r_nn'] and player_lower in self.engines:
            # Get configuration from specialized engines
            engine_obj = self.engines[player_lower]
            return {
                'name': getattr(engine_obj, 'name', player_lower),
                'version': getattr(engine_obj, 'version', 'unknown'),
                'search_algorithm': getattr(engine_obj, 'search_algorithm', player_lower),
                'depth': getattr(engine_obj, 'depth', 0),
                'max_depth': getattr(engine_obj, 'max_depth', 0)
            }
        else:
            # Default configuration for unknown engines
            return {
                'name': player_name,
                'version': 'unknown',
                'search_algorithm': 'unknown',
                'depth': 0,
                'max_depth': 0
            }

    # =============================================
    # ============ MAIN GAME LOOP =================

    def run(self, debug_mode: Optional[bool] = False):
        running = True
        game_count_remaining = self.game_count
        
        print(f"White: {self.white_player} vs Black: {self.black_player}")
        if self.monitoring_enabled and self.logger:
            self.logger.info(f"White: {self.white_player} vs Black: {self.black_player}")

        while running and game_count_remaining >= 1:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
            
            if not self.board.is_game_over() and self.board.is_valid():
                self.process_engine_move()
            else:
                if self.handle_game_end():
                    game_count_remaining -= 1
                    if game_count_remaining == 0:
                        running = False
                        if self.monitoring_enabled and self.logger:
                            self.logger.info(f'All {self.game_count} games complete, exiting...')
                        print(f'All {self.game_count} games complete, exiting...')
                    else:
                        if self.monitoring_enabled and self.logger:
                            self.logger.info(f'Game {self.game_count - game_count_remaining}/{self.game_count} complete, starting next...')
                        print(f'Game {self.game_count - game_count_remaining}/{self.game_count} complete, starting next...')
                        self.game_start_timestamp = get_timestamp()
                        self.current_game_db_id = f"eval_game_{self.game_start_timestamp}.pgn"
                        self.new_game()

            # Update the game state and render if necessary
            self.clock.tick(MAX_FPS)

            # Debug mode: pause the game state for examination
            if debug_mode:
                print("Debug mode active. Press any key to continue...")
                pygame.event.wait()    

        # Cleanup engines and resources
        self.cleanup_engines()
        if pygame.get_init():
            pygame.quit()

    def cleanup_engines(self):
        """Clean up engine resources."""
        try:
            # Cleanup RL engine
            if hasattr(self, 'rl_engine') and self.rl_engine:
                self.rl_engine.cleanup()
                
            # Cleanup GA engine
            if hasattr(self, 'ga_engine') and self.ga_engine and hasattr(self.ga_engine, 'cleanup'):
                self.ga_engine.cleanup()
                
            # Cleanup NN engine
            if hasattr(self, 'nn_engine') and self.nn_engine and hasattr(self.nn_engine, 'cleanup'):
                self.nn_engine.cleanup()
                
            # Cleanup Stockfish
            if hasattr(self, 'stockfish') and self.stockfish:
                self.stockfish.quit()
                
        except Exception as e:
            if self.monitoring_enabled and self.logger:
                self.logger.error(f"[Error] Failed during engine cleanup: {e}")
            else:
                print(f"Error during engine cleanup: {e}")

    def __del__(self):
        """Destructor to ensure cleanup."""
        try:
            self.cleanup_engines()
        except:
            pass

if __name__ == "__main__":
    
    # Set up and start the game
    if CONFIG_NAME == "default_config":
        print("Using default configuration for v7p3rChess.")
        game = v7p3rChess(config_name=CONFIG_NAME)
    else:
        print(f"Using custom configuration: {CONFIG_NAME} for v7p3rChess.")
        game = v7p3rChess(config_name=CONFIG_NAME)
    game.run()
