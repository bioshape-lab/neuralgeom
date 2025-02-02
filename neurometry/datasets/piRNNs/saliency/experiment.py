"""Main training loop."""

import input_pipeline
import ml_collections
import model as model
import numpy as np
import torch
import torch.nn as nn
import utils
import wandb
from absl import logging
from clu import metric_writers, periodic_actions
from scores import GridScorer

logging.set_verbosity(logging.INFO)


class Experiment:
    def __init__(self, rng, config: ml_collections.ConfigDict, device):
        self.config = config
        self.device = device
        self.rng = rng

        # initialize models
        logging.info("==== initialize model ====")
        self.model_config = model.GridCellConfig(**config.model)
        self.model = model.GridCell(self.model_config).to(device)

        if config.model.freeze_decoder:
            logging.info("==== freeze decoder ====")
            for param in self.model.decoder.parameters():
                param.requires_grad = False

        # initialize dataset
        logging.info("==== initialize dataset ====")
        self.train_dataset = input_pipeline.TrainDataset(
            self.rng, config.data, self.model_config
        )
        self.train_iter = iter(self.train_dataset)
        eval_dataset = input_pipeline.EvalDataset(
            self.rng,
            config.integration,
            config.data.max_dr_trans,
            config.model.num_grid,
        )
        self.eval_iter = iter(eval_dataset)

        # initialize optimizer
        logging.info("==== initialize optimizer ====")
        if config.train.optimizer_type == "adam":
            self.optimizer = torch.optim.Adam(
                filter(lambda p: p.requires_grad, self.model.parameters()),
                lr=config.train.lr,
            )
        elif config.train.optimizer_type == "adam_w":
            self.optimizer = torch.optim.AdamW(
                filter(lambda p: p.requires_grad, self.model.parameters()),
                lr=config.train.lr,
            )
        elif config.train.optimizer_type == "sgd":
            self.optimizer = torch.optim.SGD(
                filter(lambda p: p.requires_grad, self.model.parameters()),
                lr=config.train.lr,
                momentum=0.9,
            )

        if config.train.load_pretrain:
            logging.info("==== load pretrain model ====")
            ckpt_model_path = config.train.pretrain_path
            logging.info(f"Loading pretrain model from {ckpt_model_path}")
            ckpt = torch.load(ckpt_model_path, map_location=device)
            self.model.load_state_dict(ckpt["state_dict"])
            # logging.info("==== load pretrained optimizer ====")
            # self.optimizer.load_state_dict(ckpt["optimizer"])
            self.starting_step = ckpt["step"]
        else:
            self.starting_step = 1

    def train_and_evaluate(self):
        """Train and evaluate model.

        Returns
        -------
        errors : list, length num_steps_train // steps_per_integration
            list of dictionaries. Each dictionary has the structure:
            - 'vanilla': float, mean error of vanilla model for path integration step
            - 'reencode': float, mean error of reencode model for path integration step
        model : GridCell
            trained model.
        """
        logging.info("==== Experiment.train_and_evaluate() ===")

        config = self.config.train
        logging.info("num_steps_train=%d", config.num_steps_train)

        writer = metric_writers.create_default_writer()

        hooks = []
        report_progress = periodic_actions.ReportProgress(
            num_train_steps=config.num_steps_train, writer=writer
        )
        hooks += [report_progress]

        train_metrics = []
        block_size = self.model_config.block_size
        num_grid = self.model_config.num_grid
        num_block = self.model_config.num_neurons // block_size

        logging.info("==== Start of training ====")
        errors = []
        with metric_writers.ensure_flushes(writer):
            for step in range(
                self.starting_step, config.num_steps_train + self.starting_step
            ):
                # logging.info(f"Training step {step}/{config.num_steps_train + self.starting_step}")
                batch_data = utils.dict_to_device(next(self.train_iter), self.device)

                if 120000 > step > 10000:
                    # lr = 0.0003
                    lr = config.lr
                # elif step < 2000:  # warm up
                #     lr = config.lr / 2000 * step + 3e-6
                # elif step > 120000:
                #     lr = 0.0003 - (step - 120000) * (
                #         0.0003 / (config.num_steps_train - 120000)
                #     )
                # else:
                #     lr = config.lr - (config.lr - 0.0003) / 10000 * step
                for param_group in self.optimizer.param_groups:
                    param_group["lr"] = lr

                self.optimizer.zero_grad()
                loss, metrics_step = self.model(batch_data, step)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    parameters=self.model.parameters(), max_norm=10
                )
                self.optimizer.step()

                if config.positive_v:
                    with torch.no_grad():
                        self.model.encoder.v.data = self.model.encoder.v.data.clamp(
                            min=0.0
                        )

                # positive b
                self.model.trans.b.data = self.model.trans.b.data.abs()

                if config.norm_v:
                    with torch.no_grad():
                        v = self.model.encoder.v.data.reshape(
                            (-1, block_size, num_grid, num_grid)
                        )
                        v_normed = nn.functional.normalize(v, dim=1) / np.sqrt(
                            num_block
                        )
                        self.model.encoder.v.data = v_normed.reshape(
                            (-1, num_grid, num_grid)
                        )

                metrics_step = utils.dict_to_numpy(metrics_step)
                train_metrics.append(metrics_step)

                # Quick indication that training is happening.
                logging.log_first_n(
                    logging.WARNING, "Finished training step %d.", 10, step
                )
                # for h in hooks:
                #     h(step)

                if step % config.steps_per_logging == 0 or step == 1:
                    train_metrics = utils.average_appended_metrics(train_metrics)
                    wandb.log(
                        {key: value for key, value in train_metrics.items()}, step=step
                    )
                    train_metrics = []

                if (
                    step == self.starting_step
                    or step % config.steps_per_large_logging == 0
                ):
                    # ckpt_dir = os.path.join(workdir, "ckpt")
                    # if not os.path.exists(ckpt_dir):
                    #     os.makedirs(ckpt_dir)
                    # self._save_checkpoint(step, ckpt_dir)
                    # visualize v, u and heatmaps.
                    with torch.no_grad():
                        x_eval = torch.rand((3, 2)) * num_grid - 0.5
                        x_eval = x_eval.to(self.device)
                        v_x_eval = self.model.encoder(x_eval)
                        x_pred, heatmaps, _ = self.model.decoder.decode(v_x_eval)

                        # add fixed point condidtion check
                        x1 = torch.arange(0, 40, 1).repeat_interleave(40)
                        x2 = torch.arange(0, 40, 1).repeat(40)
                        x1 = torch.unsqueeze(x1, 1)
                        x2 = torch.unsqueeze(x2, 1)
                        x = torch.cat((x1, x2), axis=1)

                        error_fixed = 0.0
                        error_fixed_zero = 0.0
                        loss = nn.MSELoss()

                        for i in range(40):
                            start = i * 40
                            end = start + 40
                            input = x[start:end,]
                            v_x = self.model.encoder(input.to(self.device))
                            trans_v_x = self.model.trans(
                                v_x, torch.zeros((40, 2)).to(self.device)
                            )
                            x_t, _, _ = self.model.decoder.decode(trans_v_x)
                            x_t_zero, _, _ = self.model.decoder.decode(v_x)
                            error_fixed += loss(
                                input.float().to(self.device), x_t.float()
                            )
                            error_fixed_zero += loss(
                                input.float().to(self.device), x_t_zero.float()
                            )

                        error_fixed = error_fixed / 40
                        error_fixed_zero = error_fixed_zero / 40

                        heatmaps = heatmaps.cpu().detach().numpy()[None, ...]

                        err = torch.mean(torch.sum((x_eval - x_pred) ** 2, dim=-1))

                        wandb.log(
                            {
                                "pred_x": err.item(),
                                "error_fixed": error_fixed.item(),
                                "error_fixed_zero": error_fixed_zero.item(),
                            },
                            step=step,
                        )

                if (
                    step % config.steps_per_integration == 0
                    or step == self.starting_step
                ):
                    # perform path integration
                    with torch.no_grad():
                        eval_data = utils.dict_to_device(
                            next(self.eval_iter), self.device
                        )

                        if 10000 < step < 20000:
                            scale_tensor, score, max_scale = self.grid_scale()
                            scaling = (
                                max_scale * num_grid / self.config.data.max_dr_isometry
                            )
                            scale_tensor = scale_tensor / scaling
                            self.train_dataset.scale_vector = (
                                (scale_tensor * num_grid).detach().numpy()
                            )
                            print((scale_tensor * num_grid).detach().numpy())

                            # writer.write_scalars(step, {"score": score.item()})
                            # writer.write_scalars(
                            #     step, {"scale": scale_tensor[0].item() * num_grid}
                            # )
                            # writer.write_scalars(
                            #     step,
                            #     {
                            #         "scale_mean": torch.mean(scale_tensor).item()
                            #         * num_grid
                            #     },
                            # )
                            wandb.log(
                                {
                                    "score": score.item(),
                                    "scale": scale_tensor[0].item() * num_grid,
                                    "scale_mean": torch.mean(scale_tensor).item()
                                    * num_grid,
                                },
                                step=step,
                            )

                        outputs = self.model.path_integration(**eval_data["traj"])

                        mean_err = {
                            key: torch.mean(value)
                            for key, value in outputs["err"].items()
                        }
                        mean_err = utils.dict_to_numpy(mean_err)

                        wandb.log(
                            {key: value for key, value in mean_err.items()}, step=step
                        )
                        errors.append(mean_err)

        return errors, self.model

        # if step == config.num_steps_train:
        #     ckpt_dir = os.path.join(workdir, "ckpt")
        #     if not os.path.exists(ckpt_dir):
        #         os.makedirs(ckpt_dir)
        #     self._save_checkpoint(step, ckpt_dir)

    def grid_scale(self):
        # num_interval = self.model_config.num_grid
        block_size = self.model_config.block_size
        num_block = self.model_config.num_neurons // self.model_config.block_size

        starts = [0.1] * 20
        ends = np.linspace(0.2, 1.4, num=20)

        masks_parameters = zip(starts, ends.tolist(), strict=False)

        # ncol, nrow = block_size, num_block
        weights = self.model.encoder.v.data.cpu().detach().numpy()

        scorer = GridScorer(40, ((0, 1), (0, 1)), masks_parameters)

        score_list = np.zeros(shape=[len(weights)], dtype=np.float32)
        scale_list = np.zeros(shape=[len(weights)], dtype=np.float32)
        # orientation_list = np.zeros(shape=[len(weights)], dtype=np.float32)
        sac_list = []
        # plt.figure(figsize=(int(ncol * 1.6), int(nrow * 1.6)))

        for i in range(len(weights)):
            rate_map = weights[i]
            rate_map = (rate_map - rate_map.min()) / (rate_map.max() - rate_map.min())
            """
      score, autocorr_ori, autocorr, scale, orientation, peaks = \
          gridnessScore(rateMap=rate_map, arenaDiam=1, h=1.0 /
                        (num_interval-1), corr_cutRmin=0.3)
      if (i > 64 and i < 74) or (i > 74 and i < 77) or (i > 77 and i < 89) or (i > 89 and i < 92) or (i > 92 and i < 96):
        peaks = peaks0
      else:
        peaks0 = peaks
      """
            score_60, score_90, max_60_mask, max_90_mask, sac = scorer.get_scores(
                weights[i]
            )
            sac_list.append(sac)

            score_list[i] = score_60
            # scale_list[i] = scale
            scale_list[i] = max_60_mask[1]
            # orientation_list[i] = orientation

        scale_tensor = torch.from_numpy(scale_list)
        score_tensor = torch.from_numpy(score_list)
        max_scale = torch.max(scale_tensor[score_list > 0.37])

        scale_tensor = scale_tensor.reshape((num_block, block_size))
        scale_tensor = torch.mean(scale_tensor, dim=1)

        # score_tensor = score_tensor.reshape((num_block, block_size))
        score_tensor = torch.mean(score_tensor)

        return scale_tensor, score_tensor, max_scale

    # def _save_checkpoint(self, step, ckpt_dir):
    #     """
    #     Saving checkpoints
    #     :param epoch: current epoch number
    #     :param log: logging information of the epoch
    #     :param save_best: if True, rename the saved checkpoint to 'model_best.pth'
    #     """
    #     arch = type(self.model).__name__
    #     state = {
    #         "arch": arch,
    #         "step": step,
    #         "state_dict": self.model.state_dict(),
    #         "optimizer": self.optimizer.state_dict(),
    #         "config": self.config,
    #     }
    #     model_dir = os.path.join(ckpt_dir, "model")
    #     if not os.path.exists(model_dir):
    #         os.makedirs(model_dir)
    #     model_filename = os.path.join(model_dir, f"checkpoint-step{step}.pth")
    #     logging.info(f"Saving model checkpoint: {model_filename} ...")
    #     torch.save(state, model_filename)
    #     wandb.save(model_filename)

    #     activations_dir = os.path.join(ckpt_dir, "activations")
    #     if not os.path.exists(activations_dir):
    #         os.makedirs(activations_dir)
    #     activations_filename = os.path.join(
    #         activations_dir, f"activations-step{step}.pkl"
    #     )
    #     activations = {
    #         "v": self.model.encoder.v.data.cpu().detach().numpy(),
    #         "u": self.model.decoder.u.data.cpu().detach().numpy(),
    #     }
    #     with open(activations_filename, "wb") as f:
    #         pickle.dump(activations, f)

    #     logging.info(f"Saving activations: {activations_filename} ...")
