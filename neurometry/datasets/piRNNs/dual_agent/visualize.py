import cv2
import numpy as np
from imageio import imsave
from matplotlib import pyplot as plt
from tqdm import tqdm


def concat_images(images, image_width, spacer_size):
    """Concat image horizontally with spacer"""
    spacer = np.ones([image_width, spacer_size, 4], dtype=np.uint8) * 255
    images_with_spacers = []

    image_size = len(images)

    for i in range(image_size):
        images_with_spacers.append(images[i])
        if i != image_size - 1:
            # Add spacer
            images_with_spacers.append(spacer)
    return np.hstack(images_with_spacers)


def concat_images_in_rows(images, row_size, image_width, spacer_size=4):
    """Concat images in rows"""
    column_size = len(images) // row_size
    spacer_h = (
        np.ones(
            [
                spacer_size,
                image_width * column_size + (column_size - 1) * spacer_size,
                4,
            ],
            dtype=np.uint8,
        )
        * 255
    )

    row_images_with_spacers = []

    for row in range(row_size):
        row_images = images[column_size * row : column_size * row + column_size]
        row_concated_images = concat_images(row_images, image_width, spacer_size)
        row_images_with_spacers.append(row_concated_images)

        if row != row_size - 1:
            row_images_with_spacers.append(spacer_h)

    return np.vstack(row_images_with_spacers)


def convert_to_colormap(im, cmap):
    im = cmap(im)
    return np.uint8(im * 255)


def rgb(im, cmap="jet", smooth=True):
    cmap = plt.cm.get_cmap(cmap)
    np.seterr(invalid="ignore")  # ignore divide by zero err
    im = (im - np.min(im)) / (np.max(im) - np.min(im))
    if smooth:
        im = cv2.GaussianBlur(im, (3, 3), sigmaX=1, sigmaY=0)
    im = cmap(im)
    return np.uint8(im * 255)


def plot_ratemaps(activations, n_plots, cmap="jet", smooth=True, width=16):
    images = [rgb(im, cmap, smooth) for im in activations[:n_plots]]
    return concat_images_in_rows(images, n_plots // width, activations.shape[-1])


def compute_ratemaps(
    model,
    trajectory_generator,
    options,
    res=20,
    n_avg=None,
    Ng=512,
    idxs=None,
    all_activations_flag=False,
):
    """Compute spatial firing fields"""

    if not n_avg:
        n_avg = 1000 // options.sequence_length

    if not np.any(idxs):
        idxs = np.arange(Ng)
    idxs = idxs[:Ng]

    g = np.zeros([n_avg, options.batch_size * options.sequence_length, Ng])
    pos = np.zeros([n_avg, options.batch_size * options.sequence_length, 2])

    activations = np.zeros([Ng, res, res])
    all_activations = np.zeros([Ng, res, res, n_avg])
    counts = np.zeros([res, res])

    # model = model.double()
    model.eval()

    for index in tqdm(range(n_avg), desc="Processing"):
        inputs, pos_batch, _ = trajectory_generator.get_test_batch()
        inputs = (inputs[0].double(), inputs[1].double())
        g_batch = model.g(inputs).detach().cpu().numpy()

        pos_batch = pos_batch[:, :, :2]
        pos_batch = np.reshape(pos_batch.cpu(), [-1, 2])
        g_batch = g_batch[:, :, idxs].reshape(-1, Ng)

        g[index] = g_batch
        pos[index] = pos_batch

        x_batch = (pos_batch[:, 0] + options.box_width / 2) / (options.box_width) * res
        y_batch = (
            (pos_batch[:, 1] + options.box_height / 2) / (options.box_height) * res
        )

        for i in range(options.batch_size * options.sequence_length):
            x = x_batch[i]
            y = y_batch[i]
            if x >= 0 and x < res and y >= 0 and y < res:
                counts[int(x), int(y)] += 1
                activations[:, int(x), int(y)] += g_batch[i, :]

                if all_activations_flag:
                    all_activations[:, int(x), int(y), index] += g_batch[i, :]

    for x in range(res):
        for y in range(res):
            if counts[x, y] > 0:
                activations[:, x, y] /= counts[x, y]

    g = g.reshape([-1, Ng])
    pos = pos.reshape([-1, 2])

    # # scipy binned_statistic_2d is slightly slower
    # activations = scipy.stats.binned_statistic_2d(pos[:,0], pos[:,1], g.T, bins=res)[0]
    rate_map = activations.reshape(Ng, -1)

    if all_activations_flag:
        activations = all_activations

    return activations, rate_map, g, pos


def compute_ratemaps_single_agent(
    model, trajectory_generator, options, res=20, n_avg=None, Ng=512, idxs=None
):
    """Compute spatial firing fields"""

    if not n_avg:
        n_avg = 1000 // options.sequence_length

    if not np.any(idxs):
        idxs = np.arange(Ng)
    idxs = idxs[:Ng]

    g = np.zeros([n_avg, options.batch_size * options.sequence_length, Ng])
    pos = np.zeros([n_avg, options.batch_size * options.sequence_length, 2])

    activations = np.zeros([Ng, res, res])
    counts = np.zeros([res, res])

    for index in range(n_avg):
        inputs, pos_batch, _ = trajectory_generator.get_test_batch_single_agent()
        g_batch = model.g(inputs).detach().cpu().numpy()

        pos_batch = pos_batch[:, :, :2]
        pos_batch = np.reshape(pos_batch.cpu(), [-1, 2])
        g_batch = g_batch[:, :, idxs].reshape(-1, Ng)

        g[index] = g_batch
        pos[index] = pos_batch

        x_batch = (pos_batch[:, 0] + options.box_width / 2) / (options.box_width) * res
        y_batch = (
            (pos_batch[:, 1] + options.box_height / 2) / (options.box_height) * res
        )

        for i in range(options.batch_size * options.sequence_length):
            x = x_batch[i]
            y = y_batch[i]
            if x >= 0 and x < res and y >= 0 and y < res:
                counts[int(x), int(y)] += 1
                activations[:, int(x), int(y)] += g_batch[i, :]

    for x in range(res):
        for y in range(res):
            if counts[x, y] > 0:
                activations[:, x, y] /= counts[x, y]

    g = g.reshape([-1, Ng])
    pos = pos.reshape([-1, 2])

    # # scipy binned_statistic_2d is slightly slower
    # activations = scipy.stats.binned_statistic_2d(pos[:,0], pos[:,1], g.T, bins=res)[0]
    rate_map = activations.reshape(Ng, -1)

    return activations, rate_map, g, pos


def save_ratemaps(model, trajectory_generator, options, step, res=20, n_avg=None):
    if not n_avg:
        n_avg = 1000 // options.sequence_length
    activations, rate_map, g, pos = compute_ratemaps(
        model, trajectory_generator, options, res=res, n_avg=n_avg
    )
    rm_fig = plot_ratemaps(activations, n_plots=len(activations))
    imdir = options.save_dir + "/" + options.run_ID
    imsave(imdir + "/" + str(step) + ".png", rm_fig)

    # activations_single_agent, rate_map_single_agent, g_single_agent, pos_single_agent = compute_ratemaps_single_agent(model, trajectory_generator,
    #                                                   options, res=res, n_avg=n_avg)
    # rm_fig_single_agent = plot_ratemaps(activations_single_agent, n_plots=len(activations_single_agent))
    # imdir = options.save_dir + "/" + options.run_ID
    # imsave(imdir + "/" + str(step) + '_single_agent_ratemap' + ".png", rm_fig_single_agent)


# TODO: FIX this function
# def save_autocorr(sess, model, save_name, trajectory_generator, step, flags):
#     starts = [0.2] * 10
#     ends = np.linspace(0.4, 1.0, num=10)
#     coord_range = ((-1.1, 1.1), (-1.1, 1.1))
#     masks_parameters = zip(starts, ends.tolist(), strict=False)
#     latest_epoch_scorer = scores.GridScorer(20, coord_range, masks_parameters)

#     res = dict()
#     index_size = 100
#     for _ in range(index_size):
#         feed_dict = trajectory_generator.feed_dict(flags.box_width, flags.box_height)
#         mb_res = sess.run(
#             {
#                 "pos_xy": model.target_pos,
#                 "bottleneck": model.g,
#             },
#             feed_dict=feed_dict,
#         )
#         res = utils.concat_dict(res, mb_res)

#     filename = save_name + "/autocorrs_" + str(step) + ".pdf"
#     imdir = flags.save_dir + "/"
#     utils.get_scores_and_plot(
#         latest_epoch_scorer, res["pos_xy"], res["bottleneck"], imdir, filename
#     )
