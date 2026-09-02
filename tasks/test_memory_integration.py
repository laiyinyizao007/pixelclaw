#!/usr/bin/env python3
"""
Test script for Memory System Integration

This script tests the integration between PixelClaw and the new memory system
without requiring an actual device connection.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from PIL import Image
import numpy as np

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

try:
    from pixelclaw.memory import MemoryManager, MemoryConfig
    from pixelclaw.memory.schemas import MemoryQuery
    print("✅ Successfully imported memory system components")
except ImportError as e:
    print(f"❌ Failed to import memory system: {e}")
    sys.exit(1)


class MemorySystemTest:
    """Test suite for memory system integration."""

    def __init__(self):
        """Initialize test environment."""
        # Create temporary directory for test database
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_memory.db")

        # Test configuration
        self.config = {
            "enabled": True,
            "storage_backend": "sqlite",
            "connection_string": f"sqlite:///{self.db_path}",
            "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
            "embedding_dimension": 384,
            "qmd_index_path": os.path.join(self.temp_dir, "qmd_index"),
            "qmd_search_k": 5,
            "include_screenshots": True,
            "semantic_extraction": True,
            "async_storage": False,  # Synchronous for testing
            "max_context_actions": 8,
            "similarity_threshold": 0.5,
            "temporal_weight": 0.3,
            "max_embedding_batch_size": 16,
            "cache_size": 100,
            "cleanup_days": 7
        }

        self.memory_manager = None
        self.test_results = []

    async def setup(self):
        """Set up test environment."""
        print("🔧 Setting up test environment...")

        # Initialize memory manager
        self.memory_manager = MemoryManager(self.config)

        if not self.memory_manager.is_enabled():
            raise RuntimeError("Memory system failed to initialize")

        # Create test directories
        os.makedirs(self.config["qmd_index_path"], exist_ok=True)

        print(f"✅ Test environment ready")
        print(f"   Database: {self.db_path}")
        print(f"   QMD Index: {self.config['qmd_index_path']}")

    async def test_basic_functionality(self):
        """Test basic memory system functionality."""
        print("\n📝 Testing basic functionality...")

        try:
            # Test 1: Memory manager initialization
            assert self.memory_manager is not None
            assert self.memory_manager.is_enabled()
            print("  ✅ Memory manager initialized correctly")

            # Test 2: Configuration loading
            config = self.memory_manager.config
            assert config.enabled == True
            assert config.storage_backend == "sqlite"
            print("  ✅ Configuration loaded correctly")

            # Test 3: Layer initialization
            assert self.memory_manager.capture is not None
            assert self.memory_manager.storage is not None
            assert self.memory_manager.retrieval is not None
            print("  ✅ All three layers initialized")

            self.test_results.append(("basic_functionality", True, "All basic tests passed"))

        except Exception as e:
            print(f"  ❌ Basic functionality test failed: {e}")
            self.test_results.append(("basic_functionality", False, str(e)))

    async def test_action_capture(self):
        """Test action capture functionality."""
        print("\n🎯 Testing action capture...")

        try:
            # Create test screenshot
            test_image = Image.fromarray(
                np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
            )

            # Test action data
            test_action = {
                "action": "TAP",
                "params": {"x": 500, "y": 800}
            }

            test_result = {
                "success": True,
                "execution_time": 1.5,
                "error_message": None
            }

            test_context = {
                "step": 1,
                "goal": "Test goal: tap login button",
                "screenshot": test_image,
                "strategy_level": "step1v"
            }

            # Capture action
            success = await self.memory_manager.capture_action(
                action=test_action,
                result=test_result,
                context=test_context
            )

            assert success == True
            print("  ✅ Action captured successfully")

            # Check if memory record was created
            assert self.memory_manager.current_memory is not None
            assert len(self.memory_manager.current_memory.action_sequence) == 1
            print("  ✅ Memory record created with action")

            self.test_results.append(("action_capture", True, "Action capture successful"))

        except Exception as e:
            print(f"  ❌ Action capture test failed: {e}")
            self.test_results.append(("action_capture", False, str(e)))

    async def test_memory_storage(self):
        """Test memory storage and retrieval."""
        print("\n💾 Testing memory storage...")

        try:
            # Add more test actions to build a meaningful memory record
            for i in range(2, 5):
                test_action = {
                    "action": "TAP" if i % 2 else "TYPE",
                    "params": {"x": 100 + i * 50, "y": 200 + i * 50} if i % 2 else {"text": f"test{i}"}
                }

                test_result = {
                    "success": True,
                    "execution_time": 0.8 + i * 0.1,
                    "error_message": None
                }

                test_context = {
                    "step": i,
                    "goal": "Test goal: tap login button",
                    "screenshot": None,  # Skip screenshot for subsequent actions
                    "strategy_level": "step1v"
                }

                await self.memory_manager.capture_action(
                    action=test_action,
                    result=test_result,
                    context=test_context
                )

            # Finalize memory record
            success = await self.memory_manager.finalize_memory_record("success")
            assert success == True
            print("  ✅ Memory record finalized and stored")

            # Verify storage statistics
            stats = self.memory_manager.get_stats()
            assert stats['captures'] >= 4
            assert stats['storage_ops'] >= 1
            print(f"  ✅ Storage statistics: {stats['captures']} captures, {stats['storage_ops']} storage ops")

            self.test_results.append(("memory_storage", True, "Memory storage successful"))

        except Exception as e:
            print(f"  ❌ Memory storage test failed: {e}")
            self.test_results.append(("memory_storage", False, str(e)))

    async def test_context_enhancement(self):
        """Test enhanced context retrieval."""
        print("\n🔍 Testing context enhancement...")

        try:
            # Test enhanced context retrieval for a similar goal
            enhanced_context = await self.memory_manager.get_enhanced_context(
                goal="Test goal: tap login button",
                action_history=[
                    {"step": 1, "action": {"action": "TAP", "params": {"x": 300, "y": 400}}}
                ],
                current_screenshot=None
            )

            # Verify enhanced context structure
            assert isinstance(enhanced_context, dict)
            assert "action_history" in enhanced_context
            assert "relevant_memories" in enhanced_context
            assert "success_patterns" in enhanced_context
            assert "failure_warnings" in enhanced_context
            assert "suggested_actions" in enhanced_context
            print("  ✅ Enhanced context structure correct")

            # Check if we got enhanced data
            if enhanced_context["relevant_memories"]:
                print(f"  ✅ Retrieved {len(enhanced_context['relevant_memories'])} relevant memories")
            else:
                print("  ℹ️ No relevant memories found (expected for new database)")

            # Check action history enhancement
            action_history_count = len(enhanced_context["action_history"])
            print(f"  ✅ Enhanced action history contains {action_history_count} actions")

            self.test_results.append(("context_enhancement", True, "Context enhancement working"))

        except Exception as e:
            print(f"  ❌ Context enhancement test failed: {e}")
            self.test_results.append(("context_enhancement", False, str(e)))

    async def test_cross_task_learning(self):
        """Test cross-task learning capability."""
        print("\n🧠 Testing cross-task learning...")

        try:
            # Create a second task memory
            for i in range(1, 4):
                test_action = {
                    "action": "SWIPE" if i == 1 else "TAP",
                    "params": {
                        "x1": 100, "y1": 100, "x2": 200, "y2": 200
                    } if i == 1 else {"x": 150 + i * 30, "y": 250 + i * 30}
                }

                test_result = {
                    "success": True,
                    "execution_time": 0.9,
                    "error_message": None
                }

                test_context = {
                    "step": i,
                    "goal": "Different goal: navigate to settings",
                    "screenshot": None,
                    "strategy_level": "step1v"
                }

                await self.memory_manager.capture_action(
                    action=test_action,
                    result=test_result,
                    context=test_context
                )

            # Finalize second memory record
            await self.memory_manager.finalize_memory_record("success")
            print("  ✅ Second task memory recorded")

            # Test retrieval with goal similarity
            enhanced_context = await self.memory_manager.get_enhanced_context(
                goal="Another goal: tap settings button",
                action_history=[],
                current_screenshot=None
            )

            # Should potentially find relevant memories from different but related tasks
            memories_found = len(enhanced_context["relevant_memories"])
            print(f"  ✅ Cross-task retrieval found {memories_found} relevant memories")

            self.test_results.append(("cross_task_learning", True, f"Found {memories_found} cross-task memories"))

        except Exception as e:
            print(f"  ❌ Cross-task learning test failed: {e}")
            self.test_results.append(("cross_task_learning", False, str(e)))

    async def test_performance(self):
        """Test performance characteristics."""
        print("\n⚡ Testing performance...")

        try:
            # Test capture performance
            start_time = time.time()

            for i in range(10):
                test_action = {"action": "TAP", "params": {"x": i * 10, "y": i * 10}}
                test_result = {"success": True, "execution_time": 0.5, "error_message": None}
                test_context = {
                    "step": i,
                    "goal": "Performance test goal",
                    "screenshot": None,
                    "strategy_level": "step1v"
                }

                await self.memory_manager.capture_action(
                    action=test_action,
                    result=test_result,
                    context=test_context
                )

            capture_time = time.time() - start_time
            print(f"  ✅ Captured 10 actions in {capture_time:.3f}s ({capture_time/10:.3f}s per action)")

            # Test retrieval performance
            start_time = time.time()

            for i in range(5):
                enhanced_context = await self.memory_manager.get_enhanced_context(
                    goal=f"Test goal {i}",
                    action_history=[],
                    current_screenshot=None
                )

            retrieval_time = time.time() - start_time
            print(f"  ✅ Retrieved context 5 times in {retrieval_time:.3f}s ({retrieval_time/5:.3f}s per retrieval)")

            # Finalize performance test memory
            await self.memory_manager.finalize_memory_record("success")

            # Check final statistics
            stats = self.memory_manager.get_stats()
            print(f"  📊 Final stats: {stats}")

            self.test_results.append(("performance", True, f"Capture: {capture_time/10:.3f}s, Retrieval: {retrieval_time/5:.3f}s"))

        except Exception as e:
            print(f"  ❌ Performance test failed: {e}")
            self.test_results.append(("performance", False, str(e)))

    def cleanup(self):
        """Clean up test environment."""
        print("\n🧹 Cleaning up test environment...")

        try:
            # Remove temporary files
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            print("  ✅ Temporary files cleaned up")
        except Exception as e:
            print(f"  ⚠️ Cleanup warning: {e}")

    def print_results(self):
        """Print test results summary."""
        print("\n" + "="*60)
        print("📊 TEST RESULTS SUMMARY")
        print("="*60)

        passed = 0
        failed = 0

        for test_name, success, message in self.test_results:
            status = "✅ PASS" if success else "❌ FAIL"
            print(f"{status} {test_name}: {message}")

            if success:
                passed += 1
            else:
                failed += 1

        print(f"\n📈 Total: {passed + failed} tests, {passed} passed, {failed} failed")

        if failed == 0:
            print("🎉 ALL TESTS PASSED! Memory system integration is working correctly.")
            return True
        else:
            print(f"💥 {failed} TEST(S) FAILED! Please check the implementation.")
            return False


async def main():
    """Run all memory system integration tests."""
    print("🚀 Starting Memory System Integration Tests")
    print("="*60)

    test_suite = MemorySystemTest()

    try:
        # Setup
        await test_suite.setup()

        # Run tests
        await test_suite.test_basic_functionality()
        await test_suite.test_action_capture()
        await test_suite.test_memory_storage()
        await test_suite.test_context_enhancement()
        await test_suite.test_cross_task_learning()
        await test_suite.test_performance()

        # Print results
        success = test_suite.print_results()

        return success

    except Exception as e:
        print(f"\n💥 Test suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        test_suite.cleanup()


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        exit_code = 0 if success else 1
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n⏹️ Tests interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        sys.exit(1)